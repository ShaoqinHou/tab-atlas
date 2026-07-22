function resourceNoteSection(resource) {
  const section = node("section", "detail-section resource-notes");
  const heading = node("div", "note-heading");
  heading.append(node("h3", "", "Your note"));
  if (Number(resource.noteCount || 0)) heading.append(node("span", "note-count", formatNumber(resource.noteCount)));
  section.append(heading);

  if (!workspace.interactive) {
    section.append(node("p", "detail-note", Number(resource.noteCount || 0) ? "Notes are protected in the interactive workspace." : "No note recorded."));
    return section;
  }

  const notes = workspace.noteCache.get(resource.resourceId);
  if (!notes) {
    section.append(node("p", "note-loading", "Loading notes"));
    return section;
  }
  if (notes.length) {
    const list = node("div", "note-list");
    for (const note of notes) list.append(noteEntry(note));
    section.append(list);
  }

  const form = node("form", "note-form");
  const textarea = document.createElement("textarea");
  textarea.rows = 3;
  textarea.maxLength = 32768;
  textarea.placeholder = "What matters about this resource?";
  textarea.setAttribute("aria-label", `Add a note to ${resourceDisplayTitle(resource)}`);
  const actions = node("div", "note-form-actions");
  const record = node("button", "secondary-command note-record", workspace.recording?.resourceId === resource.resourceId ? "Stop recording" : "Record voice");
  record.type = "button";
  record.addEventListener("click", () => toggleVoiceRecording(resource.resourceId));
  const save = node("button", "primary-command", "Save note");
  save.type = "submit";
  actions.append(record, save);
  form.append(textarea, actions);
  form.addEventListener("submit", async event => {
    event.preventDefault();
    const text = textarea.value;
    if (!text.trim()) return;
    save.disabled = true;
    try {
      await workspaceRequest(`/api/v1/resources/${resource.resourceId}/notes`, {
        method: "POST",
        json: { text }
      });
      supersedeResourceProposals(resource.resourceId);
      textarea.value = "";
      resource.noteCount = Number(resource.noteCount || 0) + 1;
      workspace.noteCache.delete(resource.resourceId);
      await loadResourceWorkspaceState(resource.resourceId, true);
      elements.actionStatus.textContent = "Note saved locally. Ask Codex when you want an organization suggestion.";
    } catch (error) {
      showWorkspaceError(error, "The note could not be saved.");
    } finally {
      save.disabled = false;
    }
  });
  section.append(form);

  const auditId = workspace.latestAuditByResource.get(resource.resourceId);
  if (auditId) {
    const undo = node("button", "text-command note-undo", "Undo latest organization");
    undo.type = "button";
    undo.addEventListener("click", () => undoSemanticChange(resource.resourceId, auditId, undo));
    section.append(undo);
  }
  return section;
}

function noteEntry(note) {
  const entry = node("article", "note-entry");
  const meta = node("div", "note-meta");
  meta.append(node("strong", "", note.kind === "audio" ? "Voice note" : "Note"), node("time", "", formatDate(note.createdAt, true)));
  const remove = node("button", "text-command note-remove", "Remove note");
  remove.type = "button";
  remove.addEventListener("click", () => retractNote(note));
  meta.append(remove);
  entry.append(meta);
  if (note.kind === "text") entry.append(node("p", "note-body", note.text));
  if (note.kind === "audio") {
    const audio = document.createElement("audio");
    audio.controls = true;
    audio.preload = "metadata";
    audio.src = `/api/v1/notes/${note.id}/audio`;
    entry.append(audio);
    const transcript = note.processing?.transcription;
    if (transcript?.state === "succeeded" && transcript.outputText) {
      entry.append(transcriptEditor(note, transcript.outputText, true));
    } else {
      if (transcript?.state === "queued" || transcript?.state === "running") {
        entry.append(node("p", "note-processing", "Transcribing locally. The first voice note may take longer while the model is prepared."));
      } else if (transcript?.state === "failed") {
        entry.append(node("p", "note-processing is-error", "Automatic transcription was unavailable. The recording is safe; add a transcript below."));
      }
      entry.append(transcriptEditor(note, "", false));
    }
  }
  const interpretation = note.processing?.interpretation;
  if (interpretation?.state === "succeeded" && interpretation.outputText) {
    const meaning = node("div", "note-meaning");
    meaning.append(node("strong", "", "Meaning"), node("p", "", interpretation.outputText));
    entry.append(meaning);
  } else if (interpretation?.state === "queued" || interpretation?.state === "running") {
    entry.append(node("p", "note-processing", "Codex interpretation queued"));
  } else if (interpretation?.state === "failed") {
    entry.append(node("p", "note-processing is-error", "Interpretation pending a later Codex session"));
  }
  entry.append(noteReviewControls(note));
  return entry;
}

function transcriptEditor(note, transcript, correcting) {
  const form = node("form", "transcript-form");
  const label = node("label", "transcript-label", correcting ? "Transcript" : "Transcript fallback");
  const input = document.createElement("textarea");
  input.rows = correcting ? 3 : 2;
  input.maxLength = 32768;
  input.value = transcript;
  input.placeholder = "Type or correct what you said";
  input.setAttribute("aria-label", correcting ? "Correct voice note transcript" : "Voice note transcript fallback");
  const submit = node("button", "secondary-command", correcting ? "Save correction" : "Use typed transcript");
  submit.type = "submit";
  form.append(label, input, submit);
  form.addEventListener("submit", async event => {
    event.preventDefault();
    if (!input.value.trim() || (correcting && input.value === transcript)) return;
    submit.disabled = true;
    try {
      await workspaceRequest(`/api/v1/notes/${note.id}/transcript`, {
        method: "POST",
        json: { text: input.value }
      });
      supersedeResourceProposals(note.resourceId);
      await loadResourceWorkspaceState(note.resourceId, true);
      elements.actionStatus.textContent = "Transcript saved. Ask Codex when you want a new organization suggestion.";
    } catch (error) {
      showWorkspaceError(error, "The transcript could not be saved.");
    } finally {
      submit.disabled = false;
    }
  });
  return form;
}

function noteReviewControls(note) {
  const controls = node("div", "note-review-controls");
  const analysis = note.analysis || {};
  const canAnalyze = note.kind === "text" || (
    note.processing?.transcription?.state === "succeeded"
    && note.processing?.transcription?.outputText
  );
  const activeRequest = analysis.inputCurrent && ["queued", "running"].includes(analysis.status);
  const pendingProposal = analysis.inputCurrent && analysis.proposalId && !analysis.decision;
  const review = node(
    "button",
    pendingProposal ? "primary-command" : "secondary-command",
    !canAnalyze ? "Waiting for transcript" : (activeRequest ? "Codex reviewing" : (pendingProposal ? "Open suggestion" : "Ask Codex to reconsider"))
  );
  review.type = "button";
  review.disabled = Boolean(activeRequest || !canAnalyze);
  review.addEventListener("click", async () => {
    if (pendingProposal) {
      await refreshWorkspaceSession();
      setAgentPanel(true);
      return;
    }
    await analyzeNoteWithCodex(note, review);
  });
  controls.append(review);
  if (canAnalyze && !activeRequest && !pendingProposal) {
    controls.append(node("span", "note-review-hint", "Codex will suggest changes; nothing is applied automatically."));
  }
  return controls;
}

async function analyzeNoteWithCodex(note, button) {
  button.disabled = true;
  state.selectedResource = note.resourceId;
  setAgentPanel(true);
  workspace.messages.push({
    role: "user",
    text: "Review this note and suggest whether the resource should be reorganized."
  });
  workspace.messages.push({ role: "status", text: "Codex is reviewing the note." });
  renderAgentPanel();
  try {
    const request = await workspaceRequest(`/api/v1/notes/${note.id}/analyze`, { method: "POST" });
    await loadResourceWorkspaceState(note.resourceId, true);
    pollAgentRequest(request.id, note.resourceId);
  } catch (error) {
    workspace.messages = workspace.messages.filter(message => !(message.role === "status" && message.text === "Codex is reviewing the note."));
    showWorkspaceError(error, "The note could not be sent to Codex.");
  } finally {
    button.disabled = false;
  }
}

function resourceActionProgressSection(resource) {
  const memberships = (resource.collections || []).filter(item => item.kind === "action_list");
  if (!memberships.length) return null;
  const byCollection = new Map((resource.actionItems || []).map(item => [item.collectionId, item]));
  const section = node("section", "detail-section action-progress-section");
  section.append(node("h3", "", "Action progress"));
  for (const membership of memberships) {
    const existing = byCollection.get(membership.id) || {};
    const item = {
      collectionId: membership.id,
      collectionName: membership.name,
      workflowKind: membership.workflowKind || "none",
      state: existing.state || "queued",
      priority: Number(existing.priority || 3),
      completedUnits: Number(existing.completedUnits || 0),
      totalUnits: existing.totalUnits == null ? null : Number(existing.totalUnits),
      dueAt: existing.dueAt || "",
      revision: Number(existing.revision || 0)
    };
    section.append(actionProgressEditor(resource, item));
  }
  return section;
}

function actionProgressEditor(resource, item) {
  const form = node("form", "action-progress-editor");
  const heading = node("div", "action-progress-heading");
  heading.append(
    node("strong", "", item.collectionName),
    node("span", "", actionProgressSummary(item))
  );
  form.append(heading);

  const fields = node("div", "action-progress-fields");
  const statusLabel = node("label", "action-progress-field");
  statusLabel.append(node("span", "", "Status"));
  const status = document.createElement("select");
  for (const [value, label] of [
    ["queued", "Queued"],
    ["in_progress", "In progress"],
    ["completed", "Completed"],
    ["snoozed", "Snoozed"],
    ["skipped", "Skipped"]
  ]) {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = label;
    option.selected = item.state === value;
    status.append(option);
  }
  statusLabel.append(status);

  const priorityLabel = node("label", "action-progress-field");
  priorityLabel.append(node("span", "", "Priority"));
  const priority = document.createElement("select");
  for (let value = 1; value <= 5; value += 1) {
    const option = document.createElement("option");
    option.value = String(value);
    option.textContent = value === 1 ? "1 / highest" : String(value);
    option.selected = item.priority === value;
    priority.append(option);
  }
  priorityLabel.append(priority);

  const unitName = ["watch_queue", "reading_queue"].includes(item.workflowKind) ? "Minutes" : "Completed";
  const completedLabel = node("label", "action-progress-field");
  completedLabel.append(node("span", "", unitName));
  const completed = document.createElement("input");
  completed.type = "number";
  completed.min = "0";
  completed.max = "100000";
  completed.step = "1";
  completed.value = String(item.completedUnits);
  completedLabel.append(completed);

  const totalLabel = node("label", "action-progress-field");
  totalLabel.append(node("span", "", "Total"));
  const total = document.createElement("input");
  total.type = "number";
  total.min = "0";
  total.max = "100000";
  total.step = "1";
  total.placeholder = "Optional";
  if (item.totalUnits != null) total.value = String(item.totalUnits);
  totalLabel.append(total);
  fields.append(statusLabel, priorityLabel, completedLabel, totalLabel);
  form.append(fields);

  if (workspace.interactive) {
    const save = node("button", "secondary-command action-progress-save", "Save progress");
    save.type = "submit";
    form.append(save);
    form.addEventListener("submit", async event => {
      event.preventDefault();
      save.disabled = true;
      try {
        const totalUnits = total.value === "" ? null : Number(total.value);
        let completedUnits = Number(completed.value || 0);
        if (status.value === "completed" && totalUnits != null) completedUnits = totalUnits;
        const result = await workspaceRequest(
          `/api/v1/resources/${resource.resourceId}/action-lists/${item.collectionId}/progress`,
          {
            method: "POST",
            json: {
              state: status.value,
              priority: Number(priority.value),
              completedUnits,
              totalUnits,
              dueAt: item.dueAt,
              expectedRevision: item.revision
            }
          }
        );
        if (result.auditId) workspace.latestAuditByResource.set(resource.resourceId, result.auditId);
        await loadResourceWorkspaceState(resource.resourceId, true);
        await refreshWorkspaceSession();
        elements.actionStatus.textContent = "Action progress saved.";
        render();
      } catch (error) {
        showWorkspaceError(error, "Action progress could not be saved.");
      } finally {
        save.disabled = false;
      }
    });
  } else {
    for (const control of fields.querySelectorAll("select,input")) control.disabled = true;
  }
  return form;
}

function actionProgressSummary(item) {
  const label = ({ queued: "Queued", in_progress: "In progress", completed: "Completed", snoozed: "Snoozed", skipped: "Skipped" })[item.state] || "Queued";
  if (item.totalUnits == null) return label;
  return `${label} / ${formatNumber(item.completedUnits)} of ${formatNumber(item.totalUnits)}`;
}

async function retractNote(note) {
  if (!window.confirm("Remove this note from active use? Its local audit evidence will be retained.")) return;
  try {
    await workspaceRequest(`/api/v1/notes/${note.id}/retract`, { method: "POST" });
    supersedeResourceProposals(note.resourceId);
    const resource = resourceById.get(note.resourceId);
    if (resource) resource.noteCount = Math.max(0, Number(resource.noteCount || 0) - 1);
    workspace.noteCache.delete(note.resourceId);
    await loadResourceWorkspaceState(note.resourceId, true);
    elements.actionStatus.textContent = "Note removed from active use.";
    render();
  } catch (error) {
    showWorkspaceError(error, "The note could not be removed.");
  }
}

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

async function toggleVoiceRecording(resourceId) {
  if (workspace.recording) {
    if (workspace.recording.resourceId !== resourceId) return;
    workspace.recording.recorder.stop();
    return;
  }
  if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder !== "function") {
    elements.actionStatus.textContent = "Voice recording is not available in this browser.";
    return;
  }
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    const mimeType = ["audio/webm;codecs=opus", "audio/webm", "audio/ogg;codecs=opus", "audio/mp4"]
      .find(value => MediaRecorder.isTypeSupported(value)) || "";
    const recorder = mimeType ? new MediaRecorder(stream, { mimeType }) : new MediaRecorder(stream);
    const chunks = [];
    const startedAt = performance.now();
    const stopTimer = window.setTimeout(() => {
      if (recorder.state === "recording") recorder.stop();
    }, 590000);
    workspace.recording = { resourceId, recorder, stream, chunks, startedAt, stopTimer };
    recorder.addEventListener("dataavailable", event => {
      if (event.data.size) chunks.push(event.data);
    });
    recorder.addEventListener("stop", async () => {
      workspace.recording = null;
      window.clearTimeout(stopTimer);
      for (const track of stream.getTracks()) track.stop();
      const blob = new Blob(chunks, { type: recorder.mimeType || "audio/webm" });
      try {
        const note = await workspaceRequest(`/api/v1/resources/${resourceId}/voice-notes`, {
          method: "POST",
          body: blob,
          contentType: blob.type || "audio/webm",
          headers: { "X-TabAtlas-Audio-Duration-Ms": String(Math.round(performance.now() - startedAt)) }
        });
        const resource = resourceById.get(resourceId);
        if (resource) resource.noteCount = Number(resource.noteCount || 0) + 1;
        await loadResourceWorkspaceState(resourceId, true);
        watchNoteTranscription(note.id, resourceId);
        elements.actionStatus.textContent = "Voice note saved. Local transcription started.";
      } catch (error) {
        showWorkspaceError(error, "The voice note could not be saved.");
      }
      if (state.selectedResource === resourceId) renderDrawer();
    });
    recorder.start(1000);
    renderDrawer();
  } catch (error) {
    showWorkspaceError(error, "Microphone access was not granted.");
  }
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
