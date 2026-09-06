import { deterministicProposal, buildAgentPrompt, parseAgentProposals } from '../organization/engine.js';

const AGENT_BATCH_SIZE=30;
const ORGANIZATION_CONTRACT='organization-v1';

export class JobRunner {
  constructor({store,runtime=null}){this.store=store;this.runtime=runtime;this.controllers=new Map();}
  async runOrganization(jobId,{useAgent=false,model,reasoningEffort,stopAfter=Infinity}={}){
    const controller=new AbortController();this.controllers.set(jobId,controller);
    let job=this.store.getJob(jobId);if(!job)throw new Error('Job not found');if(job.scope?.contractVersion&&job.scope.contractVersion!==ORGANIZATION_CONTRACT)throw new Error(`Job contract ${job.scope.contractVersion} is not compatible with ${ORGANIZATION_CONTRACT}`);
    this.store.updateJob(jobId,{status:'running',error:'',cancelled:false});
    try{
      const pending=job.items.filter(i=>!['complete','stale'].includes(i.status));
      if(useAgent&&this.runtime&&pending.length)await this.#runAgentBatches(jobId,pending,{model,reasoningEffort,stopAfter,controller});
      else await this.#runDeterministic(jobId,pending,{stopAfter,controller});
      job=this.store.getJob(jobId);
      const remaining=job.items.some(i=>i.status==='pending'), stale=job.items.some(i=>i.status==='stale'), completed=job.items.filter(i=>i.status==='complete').length;
      this.store.updateJob(jobId,{completedUnits:completed,status:controller.signal.aborted?'cancelled':remaining?'paused':stale?'partial':'complete'});
      return this.store.getJob(jobId);
    }catch(e){this.store.updateJob(jobId,{status:e.code==='cancelled'?'cancelled':'failed',error:e.message});throw e;}finally{this.controllers.delete(jobId);}
  }
  async #runAgentBatches(jobId,pending,{model,reasoningEffort,stopAfter,controller}){
    const current=[];
    for(const item of pending){
      if(current.length>=stopAfter)break;
      if(!this.store.isJobItemCurrent(jobId,item.resource_id)){this.store.updateJobItem(jobId,item.resource_id,{status:'stale',error:'Resource or evidence changed since checkpoint'});continue;}
      current.push(item);
    }
    const job=this.store.getJob(jobId), globalContext=libraryContext(this.store), existingCollections=flatten(this.store.collectionsTree()).map(c=>collectionPath(c,this.store.collectionsTree()));
    for(let start=0;start<current.length;start+=AGENT_BATCH_SIZE){
      if(controller.signal.aborted)break;
      const batch=current.slice(start,start+AGENT_BATCH_SIZE), resources=batch.map(i=>this.store.getResource(i.resource_id));
      const prompt=buildAgentPrompt(resources,{wholeLibrary:job.kind==='reorganize',existingCollections,globalContext});
      const result=await runRuntimeWithRetry(this.runtime,{text:prompt,model,reasoningEffort,outputSchema:organizationSchema(),signal:controller.signal});
      const proposals=parseAgentProposals(result.text,new Set(resources.map(r=>r.id))), returned=new Set();
      for(const prop of proposals){
        returned.add(prop.resourceId);
        if(!this.store.isJobItemCurrent(jobId,prop.resourceId)){this.store.updateJobItem(jobId,prop.resourceId,{status:'stale',error:'Resource or evidence changed while analysis was running'});continue;}
        const r=this.store.getResource(prop.resourceId);this.store.createProposal({resourceId:r.id,jobId,proposal:prop,basisRevision:r.revision});this.store.updateJobItem(jobId,r.id,{status:'complete',result:prop});
      }
      const completed=this.store.getJob(jobId).items.filter(i=>i.status==='complete').length;
      this.store.updateJob(jobId,{completedUnits:completed,cursor:start+batch.length,checkpoint:{contractVersion:ORGANIZATION_CONTRACT,batchEnd:start+batch.length,lastReturnedIds:[...returned]}});
    }
  }
  async #runDeterministic(jobId,pending,{stopAfter,controller}){
    let processed=0;
    for(const item of pending){
      if(controller.signal.aborted||processed>=stopAfter)break;
      if(!this.store.isJobItemCurrent(jobId,item.resource_id)){this.store.updateJobItem(jobId,item.resource_id,{status:'stale',error:'Resource or evidence changed since checkpoint'});continue;}
      const r=this.store.getResource(item.resource_id),prop=deterministicProposal(r,{wholeLibrary:this.store.getJob(jobId).kind==='reorganize'});this.store.createProposal({resourceId:r.id,jobId,proposal:prop,basisRevision:r.revision});this.store.updateJobItem(jobId,r.id,{status:'complete',result:prop});processed++;
      const completed=this.store.getJob(jobId).items.filter(i=>i.status==='complete').length;this.store.updateJob(jobId,{completedUnits:completed,cursor:completed,checkpoint:{contractVersion:ORGANIZATION_CONTRACT,lastResourceId:r.id}});
    }
  }
  cancel(id){this.controllers.get(id)?.abort();this.store.cancelJob(id);}
}

function libraryContext(store){
  const rows=store.listResources({view:'all',limit:100000}).items,types={},hosts={};
  for(const r of rows){types[r.resource_type]=(types[r.resource_type]||0)+1;try{const h=new URL(r.canonical_url).hostname.replace(/^www\./,'');hosts[h]=(hosts[h]||0)+1;}catch{}}
  return {resourceCount:rows.length,typeCounts:types,topHosts:Object.entries(hosts).sort((a,b)=>b[1]-a[1]).slice(0,20)};
}
function flatten(tree){return tree.flatMap(x=>[x,...flatten(x.children??[])]);}
function collectionPath(node,tree){const byId=new Map(flatten(tree).map(x=>[x.id,x]));const out=[node.name];let p=node.parent_id;while(p&&out.length<6){const n=byId.get(p);if(!n)break;out.unshift(n.name);p=n.parent_id;}return out.join(' / ');}
function organizationSchema(){return {type:'object',properties:{items:{type:'array',items:{type:'object',properties:{resourceId:{type:'string'},proposedCollections:{type:'array',items:{type:'string'},minItems:1,maxItems:5},reason:{type:'string'},contextQuality:{type:'string',enum:['strong','medium','limited']},needsContext:{type:'boolean'}},required:['resourceId','proposedCollections','reason','contextQuality','needsContext'],additionalProperties:false}}},required:['items'],additionalProperties:false};}

async function runRuntimeWithRetry(runtime,args){let last;for(let attempt=0;attempt<2;attempt++){try{return await runtime.runTask(args);}catch(e){last=e;if(args.signal?.aborted||e.code==='cancelled')throw e;}}throw last;}
