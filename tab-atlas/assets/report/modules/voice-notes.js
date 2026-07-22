// On-demand microphone capture and local voice-note upload.
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
