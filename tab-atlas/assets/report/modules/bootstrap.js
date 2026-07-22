elements.homeBrand.addEventListener("click", () => setView("home"));
elements.search.addEventListener("input", () => {
  state.search = elements.search.value.trim().toLocaleLowerCase();
  state.visibleLimit = PAGE_SIZE;
  state.selectedResource = null;
  render();
});
elements.primaryNav.addEventListener("click", event => {
  const button = event.target.closest("button[data-view]");
  if (button) setView(button.dataset.view);
});
elements.agentToggle.addEventListener("click", () => setAgentPanel(true));
elements.agentClose.addEventListener("click", () => setAgentPanel(false));
elements.agentForm.addEventListener("submit", submitAgentRequest);
elements.agentOwnership.addEventListener("click", toggleAgentOwnership);
elements.syncNow.addEventListener("click", () => startBrowserSync());
document.addEventListener("keydown", event => {
  if (event.key !== "Escape") return;
  if (activeMotionPreview) stopActiveMotionPreview();
  else if (state.selectedResource) closeDetails();
  else if (state.search) clearSearch();
});
document.addEventListener("visibilitychange", () => {
  if (document.hidden) stopActiveMotionPreview();
});
window.addEventListener("scroll", () => stopActiveMotionPreview(), { passive: true });
window.addEventListener("pagehide", clearBrowserSyncPoll);

renderFreshness();
render();
initializeWorkspace();
