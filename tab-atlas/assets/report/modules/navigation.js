function spaceCard(summary) {
  const button = node("button", "space-card");
  button.type = "button";
  const mosaic = node("div", "space-mosaic");
  const previewIds = (summary.previewResourceIds || summary.resourceIds || []).slice(0, 3);
  for (const id of previewIds) {
    const resource = resourceById.get(id);
    if (resource) mosaic.append(previewVisual(resource, "mosaic", false));
  }
  while (mosaic.childElementCount < 3) mosaic.append(node("div", "mosaic-empty"));
  const copy = node("div", "space-card-copy");
  copy.append(
    node("p", "space-count", `${formatNumber(summary.resourceCount || summary.resourceIds?.length || 0)} resources`),
    node("h3", "", summary.name),
    node("p", "space-description", summary.objective || summary.description || "Resources organized around this purpose.")
  );
  const topics = node("div", "space-topics");
  for (const topic of (summary.topTopics || []).slice(0, 3)) topics.append(node("span", "", topic.name));
  copy.append(topics);
  button.append(mosaic, copy);
  button.addEventListener("click", () => openScope("space", summary.id));
  return button;
}

function workspaceRow(summary) {
  const button = node("button", "workspace-row");
  button.type = "button";
  const resourceCount = Number(summary.resourceCount || summary.resourceIds?.length || 0);
  const copy = node("span", "workspace-copy");
  copy.append(node("strong", "", summary.name), node("span", "", summary.objective || summary.description || "Project workspace"));
  const meta = node("span", "workspace-meta");
  meta.append(node("strong", "", formatNumber(resourceCount)), node("small", "", resourceCount === 1 ? "resource" : "resources"));
  button.append(copy, meta);
  button.addEventListener("click", () => openScope("project", summary.id));
  return button;
}

function actionListRow(summary) {
  const button = node("button", "action-list-row");
  button.type = "button";
  const queued = Number(summary.stateCounts?.queued || 0);
  const active = Number(summary.stateCounts?.in_progress || 0);
  const completed = Number(summary.stateCounts?.completed || 0);
  const copy = node("span", "workspace-copy");
  copy.append(
    node("strong", "", summary.name),
    node("span", "", `${formatNumber(queued)} queued / ${formatNumber(active)} active / ${formatNumber(completed)} done`)
  );
  const meta = node("span", "workspace-meta");
  meta.append(node("strong", "", formatNumber(summary.resourceCount || summary.resourceIds?.length || 0)), node("small", "", "resources"));
  button.append(copy, meta);
  button.addEventListener("click", () => openActionList(summary));
  return button;
}

function openActionList(summary) {
  openScope("action_list", summary.id);
}

function sectionHeading(title, summary, actionLabel = "", onAction = null) {
  const header = node("header", "section-heading");
  const copy = node("div");
  copy.append(node("h2", "", title), node("p", "", summary));
  header.append(copy);
  if (actionLabel && onAction) {
    const action = node("button", "text-command", actionLabel);
    action.type = "button";
    action.addEventListener("click", onAction);
    header.append(action);
  }
  return header;
}

function pageHeading(title, summary) {
  const header = node("header", "page-heading");
  header.append(node("p", "eyebrow", "Library structure"), node("h2", "", title), node("p", "scope-summary", summary));
  return header;
}

function selectControl(label, values, selected, onChange) {
  const wrapper = node("label", "select-control");
  wrapper.append(node("span", "", label));
  const select = document.createElement("select");
  for (const value of values) {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = filterOptionLabel(label, value);
    option.selected = value === selected;
    select.append(option);
  }
  select.addEventListener("change", () => onChange(select.value));
  wrapper.append(select);
  return wrapper;
}

function sourceSelectControl(label, values, selected, onChange, allLabel) {
  const wrapper = node("label", "source-select-control");
  wrapper.append(node("span", "", label));
  const select = document.createElement("select");
  for (const value of values) {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = value === "all"
      ? allLabel
      : sourcePublisherLabel(value);
    option.selected = value === selected;
    select.append(option);
  }
  select.addEventListener("change", () => onChange(select.value));
  wrapper.append(select);
  return wrapper;
}

function sourcePublisherLabel(value) {
  return value === "__unattributed" ? "Publisher not captured" : value;
}

function libraryLensControl() {
  const control = node("div", "library-lens");
  control.setAttribute("aria-label", "Library lens");
  for (const [lens, label] of [["purpose", "Purpose"], ["source", "Source"]]) {
    const button = node("button", state.libraryLens === lens ? "active" : "", label);
    button.type = "button";
    button.setAttribute("aria-pressed", String(state.libraryLens === lens));
    button.addEventListener("click", () => openLibraryLens(lens));
    control.append(button);
  }
  return control;
}

function openLibraryLens(lens) {
  state.libraryLens = lens === "source" ? "source" : "purpose";
  setView(state.libraryLens === "source" ? "sources" : "spaces");
}

function filterOptionLabel(label, value) {
  if (value === "all") return `All ${label.toLocaleLowerCase()}s`;
  if (value === "__ungrouped") return "No browser group";
  return capitalize(value);
}

function openScope(kind, id) {
  state.view = "spaces";
  state.libraryLens = "purpose";
  state.search = "";
  elements.search.value = "";
  state.scope = { kind, id };
  state.sourceScope = null;
  state.topic = "all";
  state.focus = "all";
  state.filters = defaultFilters();
  state.visibleLimit = PAGE_SIZE;
  state.selectedResource = null;
  renderAtTop();
}

function openSourceScope(id) {
  state.view = "sources";
  state.libraryLens = "source";
  state.search = "";
  elements.search.value = "";
  state.scope = null;
  state.sourceScope = id;
  state.sourcePublisher = "all";
  state.sourceTopic = "all";
  state.filters = defaultFilters();
  state.visibleLimit = PAGE_SIZE;
  state.selectedResource = null;
  renderAtTop();
}

function selectedSourceSummary() {
  if (!state.sourceScope) return null;
  return sourceSummaries.find(summary => summary.id === state.sourceScope) || null;
}

function resourcesForSourceSummary(summary) {
  return (summary.resourceIds || [])
    .map(id => resourceById.get(id))
    .filter(Boolean)
    .sort(resourceSort);
}

function selectedScopeSummary() {
  if (!state.scope) return null;
  const summaries = state.scope.kind === "project"
    ? projectSummaries
    : (state.scope.kind === "action_list" ? actionListSummaries : spaceSummaries);
  return summaries.find(summary => summary.id === state.scope.id) || null;
}

function resourcesForSummary(summary) {
  if (Array.isArray(summary.resourceIds)) {
    return summary.resourceIds.map(id => resourceById.get(id)).filter(Boolean).sort(resourceSort);
  }
  if (state.scope?.kind === "project") {
    return resources.filter(resource => resourceProjects(resource).includes(summary.name)).sort(resourceSort);
  }
  if (state.scope?.kind === "action_list") {
    return resources.filter(resource => resourceActionLists(resource).includes(summary.name)).sort(resourceSort);
  }
  return resources.filter(resource => resourceSpace(resource) === summary.name).sort(resourceSort);
}

function scopeTopics(summary, members) {
  const counts = new Map();
  for (const resource of members) {
    for (const topic of resourceTopics(resource)) counts.set(topic, (counts.get(topic) || 0) + 1);
  }
  return [...counts.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0])).map(item => item[0]);
}

function scopeFocuses(members, topic) {
  const counts = new Map();
  for (const resource of members) {
    if (topic !== "all" && !resourceTopics(resource).includes(topic)) continue;
    for (const focus of resourceFocuses(resource, topic === "all" ? "" : topic)) {
      counts.set(focus, (counts.get(focus) || 0) + 1);
    }
  }
  return [...counts.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0])).map(item => item[0]);
}

function filterResources(items) {
  return items.filter(resource => {
    if (state.filters.format !== "all" && resource.presentation?.format !== state.filters.format) return false;
    if (state.filters.browser !== "all" && !resourceContexts(resource).some(context => context.browser === state.filters.browser)) return false;
    const groups = contextGroupTitles(resource);
    if (state.filters.group === "__ungrouped" && groups.length) return false;
    if (!['all', '__ungrouped'].includes(state.filters.group) && !groups.includes(state.filters.group)) return false;
    return true;
  });
}

function updateFilter(name, value) {
  state.filters[name] = value;
  state.visibleLimit = PAGE_SIZE;
  state.selectedResource = null;
  render();
}

function activeFilterCount() {
  return Object.values(state.filters).filter(value => value !== "all").length;
}

function defaultFilters() {
  return { format: "all", browser: "all", group: "all" };
}

function inboxResources() {
  return resources.filter(resource => !resourceSpace(resource)).sort(resourceSort);
}

function exactDuplicateResources() {
  return trackedResources.filter(resource => duplicateSummary(resource).sets > 0).sort((a, b) => {
    const count = duplicateSummary(b).safeCloseCandidates - duplicateSummary(a).safeCloseCandidates;
    return count || resourceSort(a, b);
  });
}

function openTabResources() {
  return trackedResources.filter(resource => liveTabs(resource).length > 0).sort(resourceSort);
}

function safeDuplicateCount() {
  return trackedResources.reduce((total, resource) => total + duplicateSummary(resource).safeCloseCandidates, 0);
}

function duplicateSummary(resource) {
  const groups = exactDuplicateGroups(resource);
  return {
    sets: groups.length,
    instances: groups.reduce((total, group) => total + group.tabs.length - 1, 0),
    safeCloseCandidates: groups.reduce((total, group) => total + group.safe, 0),
    protectedInstances: groups.reduce((total, group) => total + group.protected, 0)
  };
}

function exactDuplicateGroups(resource) {
  const buckets = new Map();
  for (const tab of liveTabs(resource)) {
    if (!/^https:\/\//i.test(tab.url || "")) continue;
    const key = [tab.browser, tab.windowId, tab.groupId, tab.url].join("\n");
    if (!buckets.has(key)) buckets.set(key, []);
    buckets.get(key).push(tab);
  }
  const result = [];
  for (const tabs of buckets.values()) {
    if (tabs.length < 2) continue;
    const ordered = [...tabs].sort(duplicateKeeperSort);
    const candidates = ordered.slice(1);
    result.push({
      browser: ordered[0].browser,
      groupTitle: ordered[0].groupTitle || "",
      tabs: ordered,
      safe: candidates.filter(tab => !tab.active && !tab.highlighted && !tab.pinned && !tab.audible).length,
      protected: candidates.filter(tab => tab.active || tab.highlighted || tab.pinned || tab.audible).length
    });
  }
  return result;
}

function duplicateKeeperSort(a, b) {
  const rank = tab => [
    tab.active ? 0 : 1,
    tab.highlighted ? 0 : 1,
    tab.pinned ? 0 : 1,
    tab.audible ? 0 : 1,
    tab.discarded ? 1 : 0,
    Number.isFinite(Number(tab.position)) ? Number(tab.position) : Number.MAX_SAFE_INTEGER,
    String(tab.instanceId || "")
  ];
  const left = rank(a);
  const right = rank(b);
  for (let index = 0; index < left.length; index += 1) {
    if (left[index] < right[index]) return -1;
    if (left[index] > right[index]) return 1;
  }
  return 0;
}

function resourceSpace(resource) {
  if (resource.presentation?.space) return resource.presentation.space;
  const collection = (resource.collections || []).find(value => value.kind === "space");
  if (collection) return collection.name;
  return hasSpaceContract ? "" : (resource.collections?.[0]?.name || "");
}

function resourceTopics(resource) {
  if (Array.isArray(resource.presentation?.topics) && resource.presentation.topics.length) return resource.presentation.topics;
  return (resource.collections || []).filter(value => value.kind === "topic").map(value => value.name);
}

function resourceFocuses(resource, topic = "") {
  const collections = (resource.collections || []).filter(value => value.kind === "focus");
  if (topic) {
    return collections
      .filter(value => !value.parentName || value.parentName === topic)
      .map(value => value.name);
  }
  if (Array.isArray(resource.presentation?.focuses) && resource.presentation.focuses.length) {
    return resource.presentation.focuses;
  }
  return collections.map(value => value.name);
}

function resourceProjects(resource) {
  if (Array.isArray(resource.presentation?.projects) && resource.presentation.projects.length) return resource.presentation.projects;
  return (resource.collections || []).filter(value => value.kind === "project").map(value => value.name);
}

function resourceActionLists(resource) {
  if (Array.isArray(resource.presentation?.actionLists) && resource.presentation.actionLists.length) {
    return resource.presentation.actionLists;
  }
  return (resource.collections || []).filter(value => value.kind === "action_list").map(value => value.name);
}

function resourceSort(a, b) {
  const previewDifference = Number(Boolean(b.presentation?.preview?.localImage)) - Number(Boolean(a.presentation?.preview?.localImage));
  if (previewDifference) return previewDifference;
  return resourceDisplayTitle(a).localeCompare(resourceDisplayTitle(b));
}

function matchesSearch(resource) {
  const value = [
    resource.presentation?.displayTitle,
    resource.title,
    resource.displayUrl,
    resource.brief,
    resource.detail,
    resource.whyKept,
    resource.nextAction,
    resource.presentation?.source,
    resource.presentation?.sourceGroup,
    resource.presentation?.publisher,
    resource.presentation?.format,
    resource.presentation?.intent,
    resource.presentation?.contextCue,
    resourceSpace(resource),
    ...resourceTopics(resource),
    ...resourceFocuses(resource),
    ...resourceProjects(resource),
    ...resourceActionLists(resource),
    ...contextGroupTitles(resource)
  ].join("\n").toLocaleLowerCase();
  return value.includes(state.search);
}

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
