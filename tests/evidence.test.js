import test from 'node:test';
import assert from 'node:assert/strict';
import {PublicEvidenceService,assertPublicHost} from '../src/evidence/public-evidence.js';

test('public evidence rejects private hosts and revalidates redirect targets',async()=>{
  await assert.rejects(()=>assertPublicHost('127.0.0.1'),/Private\/local/);
  const fetchFn=async()=>new Response('',{status:302,headers:{location:'http://127.0.0.1/secret'}});
  const lookupFn=async(host)=>host==='public.example'?[{address:'203.0.113.10',family:4}]:[{address:'127.0.0.1',family:4}];
  const service=new PublicEvidenceService({fetchFn,lookupFn});
  const out=await service.acquire({canonical_url:'https://public.example/start',title:'x',resource_type:'web'});
  assert.equal(out.status,'limited');
  assert.match(out.limitation,/private\/local/i);
});
