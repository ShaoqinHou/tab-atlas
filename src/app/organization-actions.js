export function applyReadyProposals(store, resourceIds, {delegated=false}={}) {
  let applied=0, skipped=0;
  for (const id of resourceIds) {
    const resource=store.getResource(id);
    const proposal=resource?.proposals.find(x=>x.status==='ready');
    if(!proposal || proposal.basis_revision!==resource.revision){skipped++;continue;}
    for(const path of proposal.proposal.proposedCollections||[]){
      const collectionId=ensureCollectionPath(store,path);
      store.setMembership(id,collectionId,{origin:delegated?'agent-delegated':'agent-approved',actor:delegated?'agent-delegated':'user'});
    }
    store.decideProposal(proposal.id,'accepted');
    applied++;
  }
  return {applied,skipped};
}

export function ensureCollectionPath(store, rawPath){
  const parts=String(rawPath||'').split('/').map(x=>x.trim()).filter(Boolean).slice(0,6);
  if(!parts.length)throw new Error('Collection path is empty');
  let parentId=null;
  for(const name of parts){
    const found=flatten(store.collectionsTree()).find(x=>x.parent_id===parentId&&x.name===name);
    parentId=found?.id||store.upsertCollection({name,parentId});
  }
  return parentId;
}

function flatten(tree){return tree.flatMap(x=>[x,...flatten(x.children||[])]);}
