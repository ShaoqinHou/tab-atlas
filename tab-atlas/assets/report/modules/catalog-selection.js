// Pure catalog membership, filter, duplicate, and search selectors.
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
