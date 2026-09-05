// Note display, transcript editing, Codex review, and note retraction.
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
