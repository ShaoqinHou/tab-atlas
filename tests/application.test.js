import test from 'node:test';
import assert from 'node:assert/strict';
import {Store} from '../src/db/store.js';
import {applyReadyProposals} from '../src/app/organization-actions.js';
import {buildWorkingSet} from '../src/app/working-set.js';

test('delegated nested organization is reversible and lifecycle archive/delete can be undone',async()=>{
  const s=new Store();
  s.capture({runId:'app1',browsers:[{browser:'chrome',tabs:[{id:1,url:'https://example.com/art',title:'Reference'}]}]});
  const id=s.listResources().items[0].id;
  s.addNote(id,'Artwork for game design inspiration');
  const r=s.getResource(id);
  s.createProposal({resourceId:id,proposal:{proposedCollections:['Creative / Game design / Inspiration'],reason:'note',contextQuality:'strong'},basisRevision:r.revision});
  assert.equal(applyReadyProposals(s,[id],{delegated:true}).applied,1);
  const placed=s.getResource(id).memberships[0];assert.equal(placed.name,'Inspiration');assert.equal(placed.origin,'agent-delegated');
  const membershipHistory=s.recentHistory().find(h=>h.action_type==='membership.add');s.undo(membershipHistory.id);assert.equal(s.getResource(id).memberships.length,0);
  s.archiveResource(id,true);assert.equal(s.listResources({view:'active'}).total,0);assert.equal(s.listResources({view:'archived'}).total,1);s.undo(s.recentHistory().find(h=>h.action_type==='resource.archive'&&!h.undone_at).id);assert.equal(s.listResources({view:'active'}).total,1);
  s.deleteResource(id,true);assert.equal(s.listResources({view:'all'}).total,0);s.undo(s.recentHistory().find(h=>h.action_type==='resource.delete'&&!h.undone_at).id);assert.equal(s.listResources({view:'all'}).total,1);
  s.close();
});

test('intent working set uses confirmed notes and does not mutate library state',async()=>{
  const s=new Store();s.capture({runId:'ws',browsers:[{browser:'chrome',tabs:[{id:1,url:'https://example.com/a',title:'Pretty image'},{id:2,url:'https://example.com/b',title:'Accounting article'}]}]});
  const art=s.listResources({q:'Pretty'}).items[0].id;s.addNote(art,'Use this artwork as game inspiration');const before=s.stats();
  const ws=await buildWorkingSet({store:s,query:'game inspiration artwork',useAgent:false});assert.equal(ws.mode,'lexical');assert.equal(ws.items[0].resource.id,art);assert.deepEqual(s.stats(),before);s.close();
});

test('versioned export/import preserves resource identity, occurrence provenance, notes, and memberships',()=>{
  const a=new Store();a.capture({runId:'export1',browsers:[{browser:'chrome',profile:'Default',tabs:[{id:1,windowId:2,groupId:4,groupTitle:'Research',url:'https://export.example/x?q=1',title:'X'}]},{browser:'edge',profile:'Work',tabs:[{id:9,windowId:3,groupId:-1,url:'https://export.example/x?q=1',title:'X again'}]}]});const id=a.listResources({view:'all'}).items[0].id;a.addNote(id,'Keep for project alpha');const c=a.upsertCollection({name:'Projects'});a.setMembership(id,c);const snapshot=a.exportSnapshot();
  const b=new Store();const result=b.importSnapshot(snapshot);assert.equal(result.importedResources,1);assert.equal(result.importedOccurrences,2);const r=b.getResource(b.listResources({view:'all'}).items[0].id);assert.equal(r.occurrences.length,2);assert.ok(r.occurrences.some(o=>o.group_title==='Research'));assert.equal(r.notes[0].text,'Keep for project alpha');assert.equal(r.memberships[0].name,'Projects');a.close();b.close();
});
