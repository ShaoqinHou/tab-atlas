// Review labels and user-visible navigation transitions.
function reviewDescription(mode) {
  if (mode === "discoveries") return "Provisionally organized resources from the latest capture. Accept them into the library or dismiss them before closing browser tabs.";
  if (mode === "dismissed") return "Reviewed resources kept outside the library. Restore any mistake before running the verified close batch.";
  if (mode === "duplicates") return "Exact URL matches within the same browser window and group. Protected tabs remain visible and excluded from safe candidates.";
  if (mode === "open") return "Resources represented by the latest captured browser state. Their metadata and reopen links remain in the durable library after tabs close.";
  return "Stored or open resources without a purpose space. Organize them without keeping browser tabs alive.";
}

function reviewEmptyMessage(mode) {
  if (mode === "discoveries") return "No new resources are waiting for approval.";
  if (mode === "dismissed") return "No resources have been dismissed.";
  if (mode === "duplicates") return "No exact duplicate sets are present in the current capture.";
  if (mode === "open") return "No captured tabs are currently open. The retained library remains available in Spaces and search.";
  return "Every library resource has a purpose space.";
}

function openReview(mode) {
  state.view = "review";
  state.reviewMode = mode;
  state.search = "";
  elements.search.value = "";
  state.scope = null;
  state.sourceScope = null;
  state.visibleLimit = PAGE_SIZE;
  state.selectedResource = null;
  renderAtTop();
}

function setView(view) {
  if (view === "library") view = state.libraryLens === "source" ? "sources" : "spaces";
  state.view = ["home", "spaces", "sources", "review"].includes(view) ? view : "home";
  if (state.view === "spaces") state.libraryLens = "purpose";
  if (state.view === "sources") state.libraryLens = "source";
  state.search = "";
  elements.search.value = "";
  state.selectedResource = null;
  state.visibleLimit = PAGE_SIZE;
  if (state.view !== "spaces") state.scope = null;
  if (state.view !== "sources") state.sourceScope = null;
  if (state.view === "spaces") {
    state.scope = null;
    state.topic = "all";
    state.focus = "all";
    state.filters = defaultFilters();
  }
  if (state.view === "sources") {
    state.sourceScope = null;
    state.sourcePublisher = "all";
    state.sourceTopic = "all";
    state.filters = defaultFilters();
  }
  renderAtTop();
}

function clearSearch() {
  state.search = "";
  elements.search.value = "";
  state.visibleLimit = PAGE_SIZE;
  state.selectedResource = null;
  renderAtTop();
  elements.search.focus();
}

function openDetails(resourceId, trigger) {
  state.selectedResource = resourceId;
  state.lastFocus = trigger || document.activeElement;
  renderDrawer();
  renderAgentPanel();
}

function closeDetails() {
  const restore = state.lastFocus;
  state.selectedResource = null;
  state.lastFocus = null;
  renderDrawer();
  renderAgentPanel();
  if (restore && restore.isConnected) requestAnimationFrame(() => restore.focus({ preventScroll: true }));
}
