// Per-resource workspace cache, presentation synchronization, and semantic undo.
function supersedeResourceProposals(resourceId) {
  let changed = false;
  for (const message of workspace.messages) {
    if (message.resourceId !== resourceId || !message.proposalId || message.decision) continue;
    message.decision = "superseded";
    changed = true;
  }
  if (changed) renderAgentPanel();
}

async function loadResourceWorkspaceState(resourceId, force = false) {
  if (!workspace.interactive) return null;
  if (!force && workspace.noteLoads.has(resourceId)) return workspace.noteLoads.get(resourceId);
  const request = workspaceRequest(`/api/v1/resources/${resourceId}/workspace-state`)
    .then(result => {
      const resource = resourceById.get(resourceId);
      if (resource) {
        resource.noteCount = result.noteCount;
        resource.semanticRevision = result.semanticRevision;
        resource.collections = result.collections || [];
        resource.actionItems = result.actionItems || [];
        syncResourcePresentation(resource);
      }
      const notes = result.notes || [];
      workspace.noteCache.set(resourceId, notes);
      for (const note of notes) {
        const transcriptionState = note.processing?.transcription?.state;
        if (note.kind === "audio" && ["queued", "running"].includes(transcriptionState)) {
          watchNoteTranscription(note.id, resourceId);
        }
      }
      if (state.selectedResource === resourceId) renderDrawer();
      return result;
    })
    .catch(error => {
      showWorkspaceError(error, "Resource notes could not be loaded.");
      return null;
    })
    .finally(() => workspace.noteLoads.delete(resourceId));
  workspace.noteLoads.set(resourceId, request);
  return request;
}

function watchNoteTranscription(noteId, resourceId) {
  if (!noteId || workspace.notePolls.has(noteId)) return;
  const poll = async () => {
    try {
      const result = await loadResourceWorkspaceState(resourceId, true);
      const note = (result?.notes || []).find(item => item.id === noteId);
      const stateValue = note?.processing?.transcription?.state;
      if (["queued", "running"].includes(stateValue)) {
        const timer = window.setTimeout(poll, 1400);
        workspace.notePolls.set(noteId, timer);
        return;
      }
      workspace.notePolls.delete(noteId);
      if (stateValue === "succeeded") {
        elements.actionStatus.textContent = "Voice transcript is ready for review.";
      }
    } catch (_error) {
      workspace.notePolls.delete(noteId);
    }
  };
  const timer = window.setTimeout(poll, 800);
  workspace.notePolls.set(noteId, timer);
}

function syncResourcePresentation(resource) {
  if (!resource.presentation) resource.presentation = {};
  const ordered = [...(resource.collections || [])].sort((left, right) => authorityRank(left.authority) - authorityRank(right.authority) || String(left.name).localeCompare(String(right.name)));
  resource.presentation.space = ordered.find(item => item.kind === "space")?.name || "";
  resource.presentation.topics = ordered.filter(item => item.kind === "topic").slice(0, 2).map(item => item.name);
  resource.presentation.focuses = ordered.filter(item => item.kind === "focus").slice(0, 2).map(item => item.name);
  resource.presentation.projects = ordered.filter(item => item.kind === "project").map(item => item.name);
  resource.presentation.actionLists = ordered.filter(item => item.kind === "action_list").map(item => item.name);
}

function authorityRank(value) {
  return ({ user_locked: 0, user_note: 1, accepted_stable: 2, legacy_effective: 3, agent_inference: 4, metadata: 5 })[value] ?? 6;
}

async function undoSemanticChange(resourceId, auditId, button) {
  button.disabled = true;
  try {
    await workspaceRequest(`/api/v1/audits/${auditId}/undo`, { method: "POST" });
    workspace.latestAuditByResource.delete(resourceId);
    await loadResourceWorkspaceState(resourceId, true);
    elements.actionStatus.textContent = "Latest organization change undone.";
    render();
  } catch (error) {
    showWorkspaceError(error, "The organization change could not be undone.");
  } finally {
    button.disabled = false;
  }
}
