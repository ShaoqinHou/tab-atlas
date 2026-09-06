import { deterministicProposal, buildAgentPrompt, parseAgentProposals } from '../organization/engine.js';

export class JobRunner {
  constructor({store,runtime=null}){this.store=store;this.runtime=runtime;this.controllers=new Map();}
  async runOrganization(jobId,{useAgent=false,model,reasoningEffort,stopAfter=Infinity}={}){
    const controller=new AbortController();this.controllers.set(jobId,controller);
    let job=this.store.getJob(jobId);if(!job)throw new Error('Job not found');
    this.store.updateJob(jobId,{status:'running',error:''}); let done=job.completed_units;
    try{
      const pending=job.items.filter(i=>!['complete','stale'].includes(i.status));
      if(useAgent&&this.runtime&&pending.length){
        const current=pending.filter(i=>this.store.isJobItemCurrent(jobId,i.resource_id)).slice(0,Math.max(1,stopAfter));
        const resources=current.map(i=>this.store.getResource(i.resource_id));
        if(resources.length){
          const prompt=buildAgentPrompt(resources,{wholeLibrary:job.kind==='reorganize',existingCollections:flatten(this.store.collectionsTree()).map(c=>c.name)});
          const result=await this.runtime.runTask({text:prompt,model,reasoningEffort,outputSchema:organizationSchema(),signal:controller.signal});
          const proposals=parseAgentProposals(result.text,new Set(resources.map(r=>r.id)));
          for(const prop of proposals){
            if(!this.store.isJobItemCurrent(jobId,prop.resourceId)){this.store.updateJobItem(jobId,prop.resourceId,{status:'stale',error:'Resource or evidence changed while analysis was running'});continue;}
            const r=this.store.getResource(prop.resourceId);this.store.createProposal({resourceId:r.id,jobId,proposal:prop,basisRevision:r.revision});this.store.updateJobItem(jobId,r.id,{status:'complete',result:prop});done++;
          }
        }
      } else {
        let processed=0;
        for(const item of pending){
          if(controller.signal.aborted)break;if(processed>=stopAfter)break;
          if(!this.store.isJobItemCurrent(jobId,item.resource_id)){this.store.updateJobItem(jobId,item.resource_id,{status:'stale',error:'Resource or evidence changed since checkpoint'});continue;}
          const r=this.store.getResource(item.resource_id);const prop=deterministicProposal(r,{wholeLibrary:job.kind==='reorganize'});this.store.createProposal({resourceId:r.id,jobId,proposal:prop,basisRevision:r.revision});this.store.updateJobItem(jobId,r.id,{status:'complete',result:prop});done++;processed++;
          this.store.updateJob(jobId,{completedUnits:done,cursor:done,checkpoint:{lastResourceId:r.id}});
        }
      }
      job=this.store.getJob(jobId);const remaining=job.items.some(i=>i.status==='pending');const stale=job.items.some(i=>i.status==='stale');this.store.updateJob(jobId,{completedUnits:job.items.filter(i=>i.status==='complete').length,status:controller.signal.aborted?'cancelled':remaining?'paused':stale?'partial':'complete'});
      return this.store.getJob(jobId);
    }catch(e){this.store.updateJob(jobId,{status:e.code==='cancelled'?'cancelled':'failed',error:e.message});throw e;}finally{this.controllers.delete(jobId);}
  }
  cancel(id){this.controllers.get(id)?.abort();this.store.cancelJob(id);}
}
function flatten(tree){return tree.flatMap(x=>[x,...flatten(x.children??[])]);}

function organizationSchema(){return {type:'object',properties:{items:{type:'array',items:{type:'object',properties:{resourceId:{type:'string'},proposedCollections:{type:'array',items:{type:'string'},minItems:1,maxItems:5},reason:{type:'string'},contextQuality:{type:'string',enum:['strong','medium','limited']},needsContext:{type:'boolean'}},required:['resourceId','proposedCollections','reason','contextQuality','needsContext'],additionalProperties:false}}},required:['items'],additionalProperties:false};}
