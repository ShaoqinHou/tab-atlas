function showWorkspaceError(error, fallback) {
  const message = error?.message || fallback;
  setActionFeedback(message, "error");
  workspace.messages.push({ role: "assistant", text: message });
  renderAgentPanel();
}

function setActionFeedback(message, tone = "info") {
  elements.actionStatus.textContent = String(message || "");
  elements.actionStatus.dataset.tone = tone;
}

function renderPreservingViewport(excludedResourceIds = []) {
  const excluded = new Set(excludedResourceIds);
  const viewportTop = elements.primaryNav.getBoundingClientRect().bottom;
  const cards = [...elements.screen.querySelectorAll(".resource-card[data-resource-id]")];
  const anchor = cards.find(card => {
    if (excluded.has(card.dataset.resourceId)) return false;
    return card.getBoundingClientRect().bottom > viewportTop;
  });
  const anchorId = anchor?.dataset.resourceId || "";
  const anchorTop = anchor?.getBoundingClientRect().top ?? 0;
  const previousScroll = window.scrollY;
  render();
  requestAnimationFrame(() => {
    const nextAnchor = anchorId
      ? [...elements.screen.querySelectorAll(".resource-card[data-resource-id]")]
          .find(card => card.dataset.resourceId === anchorId)
      : null;
    if (nextAnchor) {
      window.scrollBy({ top: nextAnchor.getBoundingClientRect().top - anchorTop });
    } else {
      window.scrollTo({ top: previousScroll });
    }
  });
}

function factChip(value) {
  return node("span", "fact-chip", value);
}

function chip(value, className = "") {
  return node("span", `resource-chip ${className}`, value);
}

function cue(label, value) {
  const wrapper = node("div", "cue");
  wrapper.append(node("dt", "", label), node("dd", "", value || "Not recorded"));
  return wrapper;
}

function emptyState(message) {
  return node("div", "empty-state", message);
}

function renderAtTop() {
  render();
  window.scrollTo({ top: 0 });
}

function liveTabs(resource) {
  return Array.isArray(resource.tabs) ? resource.tabs : [];
}

function resourceContexts(resource) {
  const contexts = resource.contexts || resource.tabs;
  return Array.isArray(contexts) ? contexts : [];
}

function contextGroupTitles(resource) {
  return unique(resourceContexts(resource).map(context => context.groupTitle).filter(Boolean));
}

function libraryResourceCount() {
  return inventoryCount("libraryResources", resources.length);
}

function currentResourceCount() {
  return inventoryCount("currentResources", openTabResources().length);
}

function currentTabCount() {
  const fallback = trackedResources.reduce((total, resource) => total + liveTabs(resource).length, 0);
  return inventoryCount("currentTabs", fallback);
}

function pendingDiscoveryCount() {
  return inventoryCount("pendingDiscoveries", discoveries.length);
}

function inventoryCount(name, fallback) {
  const value = data.inventory?.[name];
  if (value === null || value === undefined || !Number.isFinite(Number(value))) return fallback;
  return Math.max(0, Number(value));
}

function resourceDisplayTitle(resource) {
  return String(resource.presentation?.displayTitle || resource.title || "Untitled resource");
}

function unique(values) {
  return [...new Set(values)].sort((a, b) => String(a).localeCompare(String(b)));
}

function node(tag, className = "", text = "") {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== "") element.textContent = text;
  return element;
}

function capitalize(value) {
  const text = String(value || "");
  return text.charAt(0).toUpperCase() + text.slice(1);
}

function formatDate(value, dateOnly = false) {
  const date = new Date(value);
  if (Number.isNaN(date.valueOf())) return value || "Unknown";
  return dateOnly ? date.toLocaleDateString() : date.toLocaleString();
}

function formatNumber(value) {
  return Number(value || 0).toLocaleString();
}
