import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'../..');
const PUBLIC=path.join(ROOT,'public');

export function createServer({store,bridge,jobs,runtime,evidence}){
  return http.createServer(async(req,res)=>{
    try{
      const url=new URL(req.url,`http://${req.headers.host||'127.0.0.1'}`);
      if(url.pathname.startsWith('/bridge/'))return await bridgeRoute(req,res,url,bridge);
      if(url.pathname.startsWith('/api/'))return await apiRoute(req,res,url,{store,bridge,jobs,runtime,evidence});
      return staticRoute(res,url.pathname);
    }catch(e){send(res,500,{error:e.message,code:e.code||'internal_error'});}
  });
}

async function apiRoute(req,res,url,{store,bridge,jobs,runtime,evidence}){
  const m=req.method;
  if(m==='GET'&&url.pathname==='/api/dashboard')return send(res,200,{stats:store.stats(),collections:store.collectionsTree(),jobs:store.listJobs(8),pairings:bridge.listPairings()});
  if(m==='GET'&&url.pathname==='/api/resources')return send(res,200,store.listResources({q:url.searchParams.get('q')||'',collectionId:url.searchParams.get('collection')||'',state:url.searchParams.get('state')||'',type:url.searchParams.get('type')||'',limit:Math.min(100,Number(url.searchParams.get('limit')||60)),offset:Number(url.searchParams.get('offset')||0)}));
  const rid=match(url.pathname,/^\/api\/resources\/([^/]+)$/);if(m==='GET'&&rid)return send(res,200,store.getResource(rid)||{error:'not_found'});
  const note=match(url.pathname,/^\/api\/resources\/([^/]+)\/notes$/);if(m==='POST'&&note){const b=await body(req);return send(res,200,store.addNote(note,b.text,{source:b.source||'typed',confirmed:b.confirmed!==false}));}
  if(m==='GET'&&url.pathname==='/api/export')return send(res,200,store.exportSnapshot());
  if(m==='POST'&&url.pathname==='/api/import'){const b=await body(req);if(b.format==='tabatlas-export')return send(res,200,store.importSnapshot(b));return send(res,200,store.capture({source:b.source||'import',runId:b.runId||null,browsers:b.browsers||[]}));}
  if(m==='POST'&&url.pathname==='/api/collections'){const b=await body(req);return send(res,201,{id:store.upsertCollection(b)});}
  if(m==='POST'&&url.pathname==='/api/memberships'){const b=await body(req);for(const resourceId of b.resourceIds||[])store.setMembership(resourceId,b.collectionId,{origin:b.origin||'user',pinned:!!b.pinned,actor:'user'});return send(res,200,{ok:true});}
  if(m==='GET'&&url.pathname==='/api/history')return send(res,200,{items:store.recentHistory()});
  const undo=match(url.pathname,/^\/api\/history\/([^/]+)\/undo$/);if(m==='POST'&&undo)return send(res,200,store.undo(undo));
  if(m==='POST'&&url.pathname==='/api/organize'){const b=await body(req);let ids=b.resourceIds||[];if(b.scope==='all'||b.scope==='filtered')ids=store.listResources({q:b.q||'',collectionId:b.collectionId||'',state:b.state||'',type:b.type||'',limit:100000}).items.map(x=>x.id);const id=store.createJob({kind:b.wholeLibrary?'reorganize':'organize',resourceIds:ids,scope:{mode:b.scope||'selected',delegated:!!b.delegated}});queueMicrotask(()=>jobs.runOrganization(id,{useAgent:!!b.useAgent,model:b.model,reasoningEffort:b.reasoningEffort}).catch(()=>{}));return send(res,202,{jobId:id});}
  const job=match(url.pathname,/^\/api\/jobs\/([^/]+)$/);if(m==='GET'&&job)return send(res,200,store.getJob(job)||{error:'not_found'});
  const cancel=match(url.pathname,/^\/api\/jobs\/([^/]+)\/cancel$/);if(m==='POST'&&cancel){jobs.cancel(cancel);return send(res,200,{ok:true});}
  const resume=match(url.pathname,/^\/api\/jobs\/([^/]+)\/resume$/);if(m==='POST'&&resume){const b=await body(req);queueMicrotask(()=>jobs.runOrganization(resume,{useAgent:!!b.useAgent,model:b.model,reasoningEffort:b.reasoningEffort}).catch(()=>{}));return send(res,202,{ok:true});}
  if(m==='POST'&&url.pathname==='/api/proposals/apply'){const b=await body(req);const resources=b.resourceIds||[];let applied=0;for(const id of resources){const r=store.getResource(id);const prop=r?.proposals.find(x=>x.status==='ready');if(!prop)continue;if(prop.basis_revision!==r.revision)continue;for(const name of prop.proposal.proposedCollections||[]){let cid=findCollection(store.collectionsTree(),name);if(!cid)cid=store.upsertCollection({name});store.setMembership(id,cid,{origin:b.delegated?'agent-delegated':'agent-approved',actor:b.delegated?'agent-delegated':'user'});}store.decideProposal(prop.id,'accepted');applied++;}return send(res,200,{applied});}
  if(m==='POST'&&url.pathname==='/api/evidence/refresh'){const b=await body(req),r=store.getResource(b.resourceId);if(!r)return send(res,404,{error:'not_found'});const ev=await evidence.acquire(r);store.addEvidence({resourceId:r.id,...ev});return send(res,200,store.getResource(r.id));}
  if(m==='POST'&&url.pathname==='/api/bridge/pair'){const b=await body(req);return send(res,201,bridge.pair(b.browser||'chrome',b.profile||'Default'));}
  if(m==='POST'&&url.pathname==='/api/bridge/capture'){const b=await body(req);const pairing=findPairingToken(bridge,b.browser,b.profile);if(!pairing)return send(res,404,{error:'not_paired'});return send(res,202,{commandId:bridge.requestCapture(pairing)});}
  if(m==='POST'&&url.pathname==='/api/bridge/save-current'){const b=await body(req);const pairing=findPairingToken(bridge,b.browser,b.profile);if(!pairing)return send(res,404,{error:'not_paired'});return send(res,202,{commandId:bridge.requestCurrentPage(pairing,{closeAfterSave:!!b.closeAfterSave,note:b.note||''})});}
  if(m==='GET'&&url.pathname==='/api/runtime/status'){try{return send(res,200,await runtime.status());}catch(e){return send(res,200,{connected:false,error:e.message,code:e.code||'runtime_error'});}}
  if(m==='POST'&&url.pathname==='/api/runtime/login'){try{return send(res,200,await runtime.startChatGPTLogin());}catch(e){return send(res,503,{error:e.message,code:e.code||'runtime_error'});}}
  return send(res,404,{error:'not_found'});
}

async function bridgeRoute(req,res,url,bridge){
  const token=req.headers.authorization?.replace(/^Bearer\s+/i,'')||url.searchParams.get('token');if(!bridge.authenticate(token))return send(res,401,{error:'unauthorized'});
  if(req.method==='GET'&&url.pathname==='/bridge/poll')return send(res,200,{command:bridge.poll(token)});
  if(req.method==='POST'&&url.pathname==='/bridge/capture-result'){const b=await body(req);return send(res,200,bridge.captureResult(token,b));}
  if(req.method==='POST'&&url.pathname==='/bridge/current-result'){const b=await body(req);return send(res,200,bridge.currentPageResult(token,b));}
  if(req.method==='POST'&&url.pathname==='/bridge/close-result'){const b=await body(req);return send(res,200,bridge.closeResult(token,b));}
  return send(res,404,{error:'not_found'});
}

function staticRoute(res,p){const rel=p==='/'?'index.html':p.replace(/^\//,'');const file=path.resolve(PUBLIC,rel);if(!file.startsWith(PUBLIC)||!fs.existsSync(file)||fs.statSync(file).isDirectory())return send(res,404,'Not found','text/plain');const ext=path.extname(file);const types={'.html':'text/html; charset=utf-8','.js':'text/javascript; charset=utf-8','.css':'text/css; charset=utf-8','.svg':'image/svg+xml'};res.writeHead(200,{'content-type':types[ext]||'application/octet-stream','cache-control':'no-store'});fs.createReadStream(file).pipe(res);}
function send(res,status,data,type='application/json; charset=utf-8'){res.writeHead(status,{'content-type':type,'access-control-allow-origin':'*'});res.end(type.startsWith('application/json')?JSON.stringify(data):data);}
async function body(req){const chunks=[];let size=0;for await(const c of req){size+=c.length;if(size>2_000_000)throw new Error('Request too large');chunks.push(c);}return chunks.length?JSON.parse(Buffer.concat(chunks).toString('utf8')):{};}
function match(path,re){return re.exec(path)?.[1]?decodeURIComponent(re.exec(path)[1]):null;}
function flatten(t){return t.flatMap(x=>[x,...flatten(x.children||[])]);}function findCollection(t,name){return flatten(t).find(x=>x.name===name)?.id||null;}
function findPairingToken(bridge,browser,profile){for(const [token,x] of bridge.tokens)if(x.browser===browser&&x.profile===(profile||'Default'))return token;return null;}
