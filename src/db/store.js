import fs from 'node:fs';
import path from 'node:path';
import { DatabaseSync } from 'node:sqlite';
import { randomUUID } from 'node:crypto';
import { schemaSql, SCHEMA_VERSION } from './schema.js';
import { normalizeHttpUrl, resourceIdForUrl } from '../domain/identity.js';

const now = () => new Date().toISOString();
const j = (v) => JSON.stringify(v ?? {});
const p = (v, fallback = {}) => { try { return JSON.parse(v); } catch { return fallback; } };

export class Store {
  constructor(dbPath=':memory:') {
    if (dbPath !== ':memory:') fs.mkdirSync(path.dirname(dbPath), {recursive:true});
    this.db = new DatabaseSync(dbPath);
    this.db.exec(schemaSql);
    this.db.prepare('INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)').run('schema_version', String(SCHEMA_VERSION));
  }
  close(){ this.db.close(); }
  tx(fn){ this.db.exec('BEGIN IMMEDIATE'); try { const out=fn(); this.db.exec('COMMIT'); return out; } catch(e){ this.db.exec('ROLLBACK'); throw e; } }
  bump(resourceId){ this.db.prepare('UPDATE resources SET revision=revision+1,updated_at=? WHERE id=?').run(now(),resourceId); }

  capture({source='import',browsers=[],runId=null}) {
    const id=runId||`cap_${randomUUID()}`, started=now();
    const prior=this.db.prepare('SELECT status,summary_json FROM capture_runs WHERE id=?').get(id);if(prior)return {...p(prior.summary_json),id,status:prior.status,replayed:true};
    this.db.prepare('INSERT INTO capture_runs(id,source,started_at,status) VALUES(?,?,?,?)').run(id,source,started,'running');
    const out={id,status:'complete',browsers:[],newResources:0,knownResources:0,occurrences:0,failures:[]};
    this.tx(()=>{
      for(const b of browsers){
        if(b.status && b.status!=='ok') { const x={browser:b.browser,profile:b.profile??'',status:b.status,error:b.error??''}; out.browsers.push(x); out.failures.push(x); continue; }
        let count=0;
        for(const tab of b.tabs??[]){
          let canonical; try{canonical=normalizeHttpUrl(tab.url);}catch{continue;}
          const rid=resourceIdForUrl(canonical), t=now();
          const exists=this.db.prepare('SELECT id FROM resources WHERE id=?').get(rid);
          if(!exists){ this.db.prepare('INSERT INTO resources(id,canonical_url,title,resource_type,created_at,updated_at) VALUES(?,?,?,?,?,?)').run(rid,canonical,tab.title??'',inferType(canonical),t,t); out.newResources++; }
          else { this.db.prepare("UPDATE resources SET title=CASE WHEN ?<>'' THEN ? ELSE title END,updated_at=? WHERE id=?").run(tab.title??'',tab.title??'',t,rid); out.knownResources++; }
          const res=this.db.prepare('INSERT OR IGNORE INTO occurrences(resource_id,browser,profile,window_key,tab_key,group_key,group_title,group_color,observed_url,observed_title,captured_at,capture_key) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)')
            .run(rid,b.browser,b.profile??'',String(tab.windowId??''),String(tab.id??''),String(tab.groupId??''),tab.groupTitle??'',tab.groupColor??'',tab.url,tab.title??'',t,`${id}:${b.browser}:${b.profile??''}`);
          if(res.changes)out.occurrences++; count++;
        }
        out.browsers.push({browser:b.browser,profile:b.profile??'',status:'ok',tabs:count});
      }
    });
    out.status=out.failures.length?(out.browsers.some(x=>x.status==='ok')?'partial':'failed'):'complete';
    this.db.prepare('UPDATE capture_runs SET completed_at=?,status=?,summary_json=? WHERE id=?').run(now(),out.status,j(out),id);
    return out;
  }

  findResourceByUrl(input){ let canonical; try{canonical=normalizeHttpUrl(input);}catch{return null;} return this.db.prepare('SELECT id FROM resources WHERE canonical_url=? AND deleted_at IS NULL').get(canonical)?.id||null; }

  listResources({q='',collectionId='',state='',type='',limit=60,offset=0}={}){
    const where=['r.deleted_at IS NULL'], args=[];
    if(q){where.push('(r.title LIKE ? OR r.canonical_url LIKE ? OR EXISTS(SELECT 1 FROM notes n WHERE n.resource_id=r.id AND n.text LIKE ?))'); const s=`%${q}%`;args.push(s,s,s);}
    if(collectionId){where.push('EXISTS(SELECT 1 FROM memberships m WHERE m.resource_id=r.id AND m.collection_id=?)');args.push(collectionId);}
    if(state){where.push('r.organization_state=?');args.push(state);}
    if(type){where.push('r.resource_type=?');args.push(type);}
    const ws=where.join(' AND '); const total=this.db.prepare(`SELECT COUNT(*) c FROM resources r WHERE ${ws}`).get(...args).c;
    const items=this.db.prepare(`SELECT r.*,
      (SELECT COUNT(*) FROM occurrences o WHERE o.resource_id=r.id) occurrence_count,
      COALESCE((SELECT text FROM notes n WHERE n.resource_id=r.id AND n.confirmed=1 ORDER BY n.updated_at DESC LIMIT 1),'') note,
      (SELECT COUNT(*) FROM proposals pr WHERE pr.resource_id=r.id AND pr.status='ready') proposal_count
      FROM resources r WHERE ${ws} ORDER BY r.updated_at DESC LIMIT ? OFFSET ?`).all(...args,limit,offset);
    return {total:Number(total),limit,offset,items:items.map(x=>({...x,occurrence_count:Number(x.occurrence_count),proposal_count:Number(x.proposal_count)}))};
  }
  getResource(id){
    const r=this.db.prepare('SELECT * FROM resources WHERE id=? AND deleted_at IS NULL').get(id); if(!r)return null;
    return {...r,
      occurrences:this.db.prepare('SELECT * FROM occurrences WHERE resource_id=? ORDER BY captured_at DESC LIMIT 100').all(id),
      notes:this.db.prepare('SELECT * FROM notes WHERE resource_id=? ORDER BY updated_at DESC').all(id),
      memberships:this.db.prepare('SELECT c.*,m.origin,m.pinned membership_pinned FROM memberships m JOIN collections c ON c.id=m.collection_id WHERE m.resource_id=?').all(id),
      evidence:this.db.prepare('SELECT * FROM evidence WHERE resource_id=? ORDER BY acquired_at DESC').all(id).map(x=>({...x,payload:p(x.payload_json)})),
      proposals:this.db.prepare('SELECT * FROM proposals WHERE resource_id=? ORDER BY created_at DESC').all(id).map(x=>({...x,proposal:p(x.proposal_json)}))};
  }
  addNote(resourceId,text,{source='typed',confirmed=true}={}){ if(!text?.trim())throw new Error('Note cannot be empty'); const id=`note_${randomUUID()}`,t=now(); this.tx(()=>{this.db.prepare('INSERT INTO notes(id,resource_id,text,source,confirmed,created_at,updated_at) VALUES(?,?,?,?,?,?,?)').run(id,resourceId,text.trim(),source,confirmed?1:0,t,t);this.bump(resourceId);});return this.getResource(resourceId); }

  upsertCollection({id=`col_${randomUUID()}`,name,parentId=null,description='',pinned=false}){ if(!name?.trim())throw new Error('Collection name required'); const t=now();this.db.prepare('INSERT INTO collections(id,parent_id,name,description,pinned,created_at,updated_at) VALUES(?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET parent_id=excluded.parent_id,name=excluded.name,description=excluded.description,pinned=excluded.pinned,updated_at=excluded.updated_at').run(id,parentId,name.trim(),description,pinned?1:0,t,t);return id; }
  collectionsTree(){ const rows=this.db.prepare('SELECT c.*,(SELECT COUNT(*) FROM memberships m WHERE m.collection_id=c.id) direct_count FROM collections c ORDER BY c.name').all(); const by=new Map(); for(const r of rows){const k=r.parent_id??'root';if(!by.has(k))by.set(k,[]);by.get(k).push(r);} const build=(k='root')=>(by.get(k)??[]).map(x=>({...x,children:build(x.id)})); return build(); }
  setMembership(resourceId,collectionId,{origin='user',pinned=false,actor='user',record=true}={}){ const old=this.db.prepare('SELECT * FROM memberships WHERE resource_id=? AND collection_id=?').get(resourceId,collectionId); if(!old)this.db.prepare('INSERT INTO memberships(resource_id,collection_id,origin,pinned,created_at) VALUES(?,?,?,?,?)').run(resourceId,collectionId,origin,pinned?1:0,now()); else this.db.prepare('UPDATE memberships SET origin=?,pinned=? WHERE resource_id=? AND collection_id=?').run(origin,pinned?1:0,resourceId,collectionId); this.db.prepare('UPDATE resources SET organization_state=?,updated_at=? WHERE id=?').run('organized',now(),resourceId); if(record&&!old)this.recordHistory('membership.add',{resourceId,collectionId,origin,pinned},{type:'membership.remove',resourceId,collectionId},actor); }
  removeMembership(resourceId,collectionId,{actor='user',record=true}={}){ const old=this.db.prepare('SELECT * FROM memberships WHERE resource_id=? AND collection_id=?').get(resourceId,collectionId);if(!old)return;this.db.prepare('DELETE FROM memberships WHERE resource_id=? AND collection_id=?').run(resourceId,collectionId);if(record)this.recordHistory('membership.remove',{resourceId,collectionId},{type:'membership.add',resourceId,collectionId,origin:old.origin,pinned:!!old.pinned},actor); }
  recordHistory(type,action,inverse,actor='user'){const id=`hist_${randomUUID()}`;this.db.prepare('INSERT INTO history(id,action_type,action_json,inverse_json,actor,created_at) VALUES(?,?,?,?,?,?)').run(id,type,j(action),j(inverse),actor,now());return id;}
  recentHistory(limit=30){return this.db.prepare('SELECT * FROM history ORDER BY created_at DESC LIMIT ?').all(limit).map(x=>({...x,action:p(x.action_json),inverse:p(x.inverse_json)}));}
  undo(id){const h=this.db.prepare('SELECT * FROM history WHERE id=?').get(id);if(!h)throw new Error('History not found');if(h.undone_at)return {alreadyUndone:true};const inv=p(h.inverse_json);if(inv.type==='membership.remove')this.removeMembership(inv.resourceId,inv.collectionId,{record:false});else if(inv.type==='membership.add')this.setMembership(inv.resourceId,inv.collectionId,{origin:inv.origin,pinned:inv.pinned,record:false});else throw new Error('Unsupported undo');this.db.prepare('UPDATE history SET undone_at=? WHERE id=?').run(now(),id);return {undone:true};}

  addEvidence({resourceId,adapter,kind,payload,status='ok',limitation='',sourceUrl='',contentHash,expiresAt=null}){const id=`ev_${randomUUID()}`;this.db.prepare('INSERT OR IGNORE INTO evidence(id,resource_id,adapter,kind,payload_json,acquired_at,expires_at,status,limitation,source_url,content_hash) VALUES(?,?,?,?,?,?,?,?,?,?,?)').run(id,resourceId,adapter,kind,j(payload),now(),expiresAt,status,limitation,sourceUrl,contentHash);this.db.prepare('UPDATE resources SET evidence_state=?,updated_at=? WHERE id=?').run(status==='ok'?'ready':'limited',now(),resourceId);return id;}
  evidenceFingerprint(rid){return j(this.db.prepare('SELECT adapter,kind,content_hash,status FROM evidence WHERE resource_id=? ORDER BY adapter,kind,content_hash').all(rid));}
  createProposal({resourceId,jobId=null,proposal,basisRevision}){const id=`prop_${randomUUID()}`;this.db.prepare('INSERT INTO proposals(id,resource_id,job_id,proposal_json,basis_revision,status,created_at) VALUES(?,?,?,?,?,?,?)').run(id,resourceId,jobId,j(proposal),basisRevision,'ready',now());this.db.prepare('UPDATE resources SET organization_state=?,updated_at=? WHERE id=?').run('suggestion_ready',now(),resourceId);return id;}
  decideProposal(id,status){this.db.prepare('UPDATE proposals SET status=?,decided_at=? WHERE id=?').run(status,now(),id);}

  createJob({kind,resourceIds,scope={}}){const id=`job_${randomUUID()}`,t=now();this.db.prepare('INSERT INTO jobs(id,kind,scope_json,status,created_at,updated_at,total_units) VALUES(?,?,?,?,?,?,?)').run(id,kind,j(scope),'queued',t,t,resourceIds.length);for(const rid of resourceIds){const r=this.db.prepare('SELECT revision FROM resources WHERE id=?').get(rid);if(r)this.db.prepare('INSERT INTO job_items(job_id,resource_id,basis_revision,evidence_fingerprint,status,updated_at) VALUES(?,?,?,?,?,?)').run(id,rid,r.revision,this.evidenceFingerprint(rid),'pending',t);}return id;}
  getJob(id){const x=this.db.prepare('SELECT * FROM jobs WHERE id=?').get(id);if(!x)return null;return {...x,scope:p(x.scope_json),checkpoint:p(x.checkpoint_json),items:this.db.prepare('SELECT * FROM job_items WHERE job_id=? ORDER BY resource_id').all(id).map(i=>({...i,result:p(i.result_json)}))};}
  listJobs(limit=20){return this.db.prepare('SELECT * FROM jobs ORDER BY updated_at DESC LIMIT ?').all(limit).map(x=>({...x,scope:p(x.scope_json),checkpoint:p(x.checkpoint_json)}));}
  updateJob(id,patch={}){const x=this.getJob(id);if(!x)throw new Error('Job not found');this.db.prepare('UPDATE jobs SET status=?,cursor=?,completed_units=?,checkpoint_json=?,error=?,cancelled=?,updated_at=? WHERE id=?').run(patch.status??x.status,patch.cursor??x.cursor,patch.completedUnits??x.completed_units,j(patch.checkpoint??x.checkpoint),patch.error??x.error,patch.cancelled===undefined?x.cancelled:(patch.cancelled?1:0),now(),id);}
  updateJobItem(jid,rid,{status,result={},error=''}){this.db.prepare('UPDATE job_items SET status=?,result_json=?,error=?,updated_at=? WHERE job_id=? AND resource_id=?').run(status,j(result),error,now(),jid,rid);}
  isJobItemCurrent(jid,rid){const i=this.db.prepare('SELECT * FROM job_items WHERE job_id=? AND resource_id=?').get(jid,rid),r=this.db.prepare('SELECT revision FROM resources WHERE id=?').get(rid);return !!i&&!!r&&i.basis_revision===r.revision&&i.evidence_fingerprint===this.evidenceFingerprint(rid);}
  cancelJob(id){this.updateJob(id,{status:'cancelled',cancelled:true});}

  createBrowserAction({id=`act_${randomUUID()}`,actionType,resourceId=null,target}){const t=now();this.db.prepare('INSERT OR IGNORE INTO browser_actions(id,action_type,resource_id,requested_target_json,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?)').run(id,actionType,resourceId,j(target),'requested',t,t);return this.getBrowserAction(id);}
  getBrowserAction(id){const a=this.db.prepare('SELECT * FROM browser_actions WHERE id=?').get(id);return a?{...a,target:p(a.requested_target_json),result:p(a.observed_result_json)}:null;}
  finishBrowserAction(id,status,result){this.db.prepare('UPDATE browser_actions SET status=?,observed_result_json=?,updated_at=? WHERE id=?').run(status,j(result),now(),id);return this.getBrowserAction(id);}
  exportSnapshot(){return {format:'tabatlas-export',version:1,exportedAt:now(),resources:this.db.prepare('SELECT * FROM resources WHERE deleted_at IS NULL').all(),occurrences:this.db.prepare('SELECT * FROM occurrences').all(),notes:this.db.prepare('SELECT * FROM notes').all(),collections:this.db.prepare('SELECT * FROM collections').all(),memberships:this.db.prepare('SELECT * FROM memberships').all(),evidence:this.db.prepare('SELECT * FROM evidence').all()};}
  importSnapshot(snapshot){
    if(snapshot?.format!=='tabatlas-export'||snapshot?.version!==1)throw new Error('Unsupported TabAtlas export format');
    const map=new Map(), t=now();
    this.tx(()=>{
      for(const r of snapshot.resources||[]){
        let canonical;try{canonical=normalizeHttpUrl(r.canonical_url);}catch{continue;}
        const existing=this.db.prepare('SELECT id FROM resources WHERE canonical_url=?').get(canonical), rid=existing?.id||resourceIdForUrl(canonical);
        if(!existing)this.db.prepare('INSERT INTO resources(id,canonical_url,title,description,resource_type,created_at,updated_at,revision,organization_state,evidence_state) VALUES(?,?,?,?,?,?,?,?,?,?)').run(rid,canonical,r.title||'',r.description||'',r.resource_type||inferType(canonical),r.created_at||t,r.updated_at||t,Math.max(1,Number(r.revision||1)),r.organization_state||'pending',r.evidence_state||'pending');
        map.set(r.id,rid);
      }
      for(const c of snapshot.collections||[])this.db.prepare('INSERT OR IGNORE INTO collections(id,parent_id,name,description,pinned,created_at,updated_at) VALUES(?,?,?,?,?,?,?)').run(c.id,c.parent_id||null,c.name,c.description||'',c.pinned?1:0,c.created_at||t,c.updated_at||t);
      for(const o of snapshot.occurrences||[]){const rid=map.get(o.resource_id);if(rid)this.db.prepare('INSERT OR IGNORE INTO occurrences(resource_id,browser,profile,window_key,tab_key,group_key,group_title,group_color,observed_url,observed_title,captured_at,capture_key) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)').run(rid,o.browser||'import',o.profile||'',o.window_key||'',o.tab_key||'',o.group_key||'',o.group_title||'',o.group_color||'',o.observed_url||this.db.prepare('SELECT canonical_url FROM resources WHERE id=?').get(rid)?.canonical_url||'',o.observed_title||'',o.captured_at||t,o.capture_key||`import:${o.id||randomUUID()}`);}
      for(const n of snapshot.notes||[]){const rid=map.get(n.resource_id);if(rid)this.db.prepare('INSERT OR IGNORE INTO notes(id,resource_id,text,source,confirmed,created_at,updated_at) VALUES(?,?,?,?,?,?,?)').run(n.id,rid,n.text,n.source||'typed',n.confirmed?1:0,n.created_at||t,n.updated_at||t);}
      for(const m of snapshot.memberships||[]){const rid=map.get(m.resource_id);if(rid)this.db.prepare('INSERT OR IGNORE INTO memberships(resource_id,collection_id,origin,pinned,created_at) VALUES(?,?,?,?,?)').run(rid,m.collection_id,m.origin||'import',m.pinned?1:0,m.created_at||t);}
      for(const e of snapshot.evidence||[]){const rid=map.get(e.resource_id);if(rid)this.db.prepare('INSERT OR IGNORE INTO evidence(id,resource_id,adapter,kind,payload_json,acquired_at,expires_at,status,limitation,source_url,content_hash) VALUES(?,?,?,?,?,?,?,?,?,?,?)').run(e.id,rid,e.adapter,e.kind,e.payload_json,e.acquired_at||t,e.expires_at||null,e.status,e.limitation||'',e.source_url||'',e.content_hash);}
    });
    return {importedResources:map.size,importedOccurrences:(snapshot.occurrences||[]).filter(o=>map.has(o.resource_id)).length};
  }

  setSetting(key,value){this.db.prepare('INSERT INTO settings(key,value_json,updated_at) VALUES(?,?,?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json,updated_at=excluded.updated_at').run(key,j(value),now());}
  getSetting(key,fallback=null){const r=this.db.prepare('SELECT value_json FROM settings WHERE key=?').get(key);return r?p(r.value_json,fallback):fallback;}
  stats(){const r=this.db.prepare("SELECT COUNT(*) total,SUM(organization_state='pending') pending,SUM(organization_state='suggestion_ready') suggestions,SUM(organization_state='organized') organized,SUM(evidence_state='limited') limited FROM resources WHERE deleted_at IS NULL").get();return Object.fromEntries(Object.entries(r).map(([k,v])=>[k,Number(v??0)]));}
}

function inferType(url){const u=new URL(url),h=u.hostname.replace(/^www\./,'');if(h==='youtube.com'||h==='youtu.be')return 'video';if(h==='x.com'||h==='twitter.com')return 'social';if(/\.(png|jpe?g|gif|webp|avif)$/i.test(u.pathname))return 'image';return 'web';}
