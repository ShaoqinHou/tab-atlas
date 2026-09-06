import { spawn } from 'node:child_process';
import readline from 'node:readline';
import { EventEmitter } from 'node:events';

export class RuntimeError extends Error { constructor(code,message,cause){super(message,{cause});this.name='RuntimeError';this.code=code;} }

export class CodexAppServer extends EventEmitter {
  constructor({bin=process.env.TABATLAS_CODEX_BIN||'codex', spawnFn=spawn, defaultCwd=null}={}){super();this.bin=bin;this.spawnFn=spawnFn;this.defaultCwd=defaultCwd;this.proc=null;this.pending=new Map();this.seq=1;this.initialized=false;}
  async connect(){
    if(this.proc)return;
    try{this.proc=this.spawnFn(this.bin,['app-server'],{stdio:['pipe','pipe','pipe'],env:safeEnv(process.env)});}catch(e){throw new RuntimeError('spawn_failed',`Could not start ${this.bin} app-server`,e);}
    const rl=readline.createInterface({input:this.proc.stdout});
    rl.on('line',line=>this.#line(line));
    this.proc.stderr?.on('data',d=>this.emit('stderr',String(d)));
    this.proc.on('exit',(code,signal)=>this.#failed(new RuntimeError('runtime_exited',`Codex app-server exited (${code??signal??'unknown'})`),{code,signal}));
    this.proc.on('error',error=>this.#failed(new RuntimeError('spawn_failed',`Could not start ${this.bin} app-server`,error),{error}));
    await this.request('initialize',{clientInfo:{name:'tabatlas',title:'TabAtlas',version:'0.1.0'}});
    this.notify('initialized',{}); this.initialized=true;
  }
  async disconnect(){if(this.proc){this.proc.kill();this.proc=null;}this.initialized=false;}
  notify(method,params={}){if(!this.proc?.stdin)throw new RuntimeError('not_connected','Codex runtime is not connected');this.proc.stdin.write(`${JSON.stringify({method,params})}\n`);}
  request(method,params={}){if(!this.proc?.stdin)throw new RuntimeError('not_connected','Codex runtime is not connected');const id=this.seq++;return new Promise((resolve,reject)=>{this.pending.set(id,{resolve,reject,method});this.proc.stdin.write(`${JSON.stringify({id,method,params})}\n`);});}
  async status(){await this.connect();const [account,models]=await Promise.all([this.request('account/read',{refreshToken:false}),this.request('model/list',{limit:100,includeHidden:false})]);return {connected:true,account:account?.account??null,requiresOpenaiAuth:!!account?.requiresOpenaiAuth,models:models?.data??[]};}
  async startChatGPTLogin(){await this.connect();return this.request('account/login/start',{type:'chatgpt',useHostedLoginSuccessPage:true,appBrand:'chatgpt'});}
  async runTask({text,model,reasoningEffort,cwd=null,outputSchema=null,onEvent,signal}){
    await this.connect();
    const runCwd=cwd||this.defaultCwd;const thread=await this.request('thread/start',{...(model?{model}:{}),...(runCwd?{cwd:runCwd}:{}),approvalPolicy:'never',sandbox:'readOnly',serviceName:'tabatlas'});
    const threadId=thread?.thread?.id; if(!threadId)throw new RuntimeError('protocol_error','thread/start returned no thread id');
    let turnId=null, output='';
    const handler=(msg)=>{if(msg?.params?.threadId && msg.params.threadId!==threadId)return;onEvent?.(msg);if(msg.method==='item/agentMessage/delta')output+=msg.params?.delta??'';if(msg.method==='turn/completed')this.emit(`turn:${threadId}`,msg);};
    this.on('notification',handler);
    const abort=async()=>{try{if(turnId)await this.request('turn/interrupt',{threadId,turnId});}catch{}}; signal?.addEventListener('abort',abort,{once:true});
    try{
      const started=await this.request('turn/start',{threadId,input:[{type:'text',text}],...(model?{model}:{}),...(reasoningEffort?{effort:reasoningEffort}:{}),...(outputSchema?{outputSchema}:{}),...(runCwd?{cwd:runCwd,sandboxPolicy:{type:'readOnly',access:{type:'restricted',includePlatformDefaults:true,readableRoots:[runCwd]}}}:{})});
      turnId=started?.turn?.id??null;
      const completed=await waitForEvent(this,`turn:${threadId}`,120000,signal);
      const status=completed?.params?.turn?.status??completed?.params?.status??'unknown';
      if(status==='failed')throw new RuntimeError('turn_failed',completed?.params?.turn?.error?.message||'Codex turn failed');
      if(status==='interrupted')throw new RuntimeError('cancelled','Codex turn was interrupted');
      return {threadId,turnId,status,text:output};
    } finally {this.off('notification',handler);signal?.removeEventListener('abort',abort);}
  }
  #failed(err,detail){for(const {reject} of this.pending.values())reject(err);this.pending.clear();this.proc=null;this.initialized=false;this.emit('exit',detail);}
  #line(line){let msg;try{msg=JSON.parse(line);}catch{return this.emit('protocol_warning',{line});}if(msg.id!==undefined&&this.pending.has(msg.id)){const p=this.pending.get(msg.id);this.pending.delete(msg.id);if(msg.error)p.reject(new RuntimeError('rpc_error',msg.error.message||JSON.stringify(msg.error)));else p.resolve(msg.result);return;}if(msg.method)this.emit('notification',msg);}
}

function waitForEvent(emitter,name,timeout,signal){return new Promise((resolve,reject)=>{const timer=setTimeout(()=>done(new RuntimeError('timeout',`Timed out waiting for ${name}`)),timeout);const on=v=>done(null,v);const abort=()=>done(new RuntimeError('cancelled','Cancelled'));function done(err,v){clearTimeout(timer);emitter.off(name,on);signal?.removeEventListener('abort',abort);err?reject(err):resolve(v);}emitter.once(name,on);signal?.addEventListener('abort',abort,{once:true});});}
function safeEnv(env){const out={};for(const [k,v] of Object.entries(env)){if(/^(PATH|PATHEXT|SYSTEMROOT|WINDIR|HOME|USERPROFILE|LOCALAPPDATA|APPDATA|TEMP|TMP|LANG|LC_|TERM|COLORTERM|CODEX_)/i.test(k))out[k]=v;}return out;}
