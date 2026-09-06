const BASE='http://127.0.0.1:8790';
const ALARM='tabatlas-poll';

chrome.runtime.onInstalled.addListener(async()=>{const s=await chrome.storage.local.get({enabled:false});if(s.enabled)enableAlarm();});
chrome.alarms.onAlarm.addListener(a=>{if(a.name===ALARM)poll().catch(()=>{});});
chrome.runtime.onMessage.addListener((msg,_sender,send)=>{handle(msg).then(send).catch(e=>send({ok:false,error:e.message}));return true;});

async function handle(msg){
  if(msg.type==='status'){const s=await chrome.storage.local.get({enabled:false,token:'',browser:'chrome',profile:'Default'});return {ok:true,...s,version:chrome.runtime.getManifest().version};}
  if(msg.type==='configure'){await chrome.storage.local.set({token:msg.token.trim(),browser:msg.browser,profile:msg.profile||'Default'});return {ok:true};}
  if(msg.type==='toggle'){await chrome.storage.local.set({enabled:!!msg.enabled});msg.enabled?enableAlarm():chrome.alarms.clear(ALARM);return {ok:true};}
  if(msg.type==='poll'){return {ok:true,ran:await poll()};}
  if(msg.type==='save-current'){return saveCurrentDirect(msg);}
  throw new Error('Unknown command');
}
function enableAlarm(){chrome.alarms.create(ALARM,{periodInMinutes:0.5});poll().catch(()=>{});}
async function auth(){const s=await chrome.storage.local.get({token:'',enabled:false,browser:'chrome',profile:'Default'});if(!s.token)throw new Error('Not paired');return s;}
async function req(path,opts={}){const s=await auth();const r=await fetch(BASE+path,{headers:{'content-type':'application/json','authorization':`Bearer ${s.token}`,...(opts.headers||{})},...opts});const x=await r.json();if(!r.ok)throw new Error(x.error||`HTTP ${r.status}`);return x;}
async function poll(){const s=await auth();if(!s.enabled)return false;const {command}=await req('/bridge/poll');if(!command)return false;if(command.type==='capture')await capture(command);else if(command.type==='save-current')await saveCurrent(command);else if(command.type==='close')await closeExact(command);return true;}
async function capture(command){try{const windows=await chrome.windows.getAll({populate:true,windowTypes:['normal']});const groups=await chrome.tabGroups.query({});const gm=new Map(groups.map(g=>[g.id,g]));const tabs=[];for(const w of windows)for(const t of w.tabs||[])if(/^https?:/i.test(t.url||'')){const g=gm.get(t.groupId);tabs.push({id:t.id,windowId:t.windowId,groupId:t.groupId,groupTitle:g?.title||'',groupColor:g?.color||'',url:t.url,title:t.title||''});}await req('/bridge/capture-result',{method:'POST',body:JSON.stringify({commandId:command.id,status:'ok',tabs})});}catch(e){await req('/bridge/capture-result',{method:'POST',body:JSON.stringify({commandId:command.id,status:'failed',error:e.message,tabs:[]})}).catch(()=>{});}}
async function saveCurrent(command){const [tab]=await chrome.tabs.query({active:true,currentWindow:true});if(!tab||!/^https?:/i.test(tab.url||''))throw new Error('Current tab is not an HTTP(S) page');const result=await req('/bridge/current-result',{method:'POST',body:JSON.stringify({commandId:command.id,tab:plainTab(tab),note:command.note||'',closeAfterSave:!!command.closeAfterSave})});if(result.close)await closeExact({id:result.close.actionId,type:'close',target:result.close.target});return result;}
async function saveCurrentDirect(msg){const [tab]=await chrome.tabs.query({active:true,currentWindow:true});if(!tab||!/^https?:/i.test(tab.url||''))throw new Error('Current tab is not an HTTP(S) page');const id=`direct_${Date.now()}`;const result=await req('/bridge/current-result',{method:'POST',body:JSON.stringify({commandId:id,tab:plainTab(tab),note:msg.note||'',closeAfterSave:!!msg.closeAfterSave})});if(result.close)await closeExact({id:result.close.actionId,target:result.close.target});return {ok:true,result};}
async function closeExact(command){const t=command.target;let observed={tabId:t.tabId,windowId:t.windowId,url:'',closed:false};try{const live=await chrome.tabs.get(t.tabId);observed.url=live.url||'';if(live.windowId!==t.windowId||live.url!==t.url)throw new Error('Tab changed since it was saved');await chrome.tabs.remove(t.tabId);observed.closed=true;}catch(e){observed.error=e.message;}await req('/bridge/close-result',{method:'POST',body:JSON.stringify({actionId:command.id,observed})});}
function plainTab(t){return {id:t.id,windowId:t.windowId,groupId:t.groupId,url:t.url,title:t.title||''};}
