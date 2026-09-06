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


test('public evidence drops private-network preview URLs from page metadata',async()=>{
  const html='<html><head><meta property="og:image" content="http://127.0.0.1/private.png"><meta name="description" content="safe text"></head><body>hello</body></html>';
  const fetchFn=async()=>new Response(html,{status:200,headers:{'content-type':'text/html'}});
  const lookupFn=async(host)=>host==='public.example'?[{address:'203.0.113.11',family:4}]:[{address:'127.0.0.1',family:4}];
  const out=await new PublicEvidenceService({fetchFn,lookupFn}).acquire({canonical_url:'https://public.example/page',title:'p',resource_type:'web'});
  assert.equal(out.status,'ok');assert.equal(out.payload.image,'');assert.equal(out.payload.description,'safe text');
});
