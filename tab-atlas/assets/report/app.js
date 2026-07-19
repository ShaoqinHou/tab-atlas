const data = window.__TAB_ATLAS__ || {
  inventory: {}, resources: [], groups: [], collectionSummaries: [], facets: {}
};
const STORAGE_KEY = "tabatlas.decisions.v1";
const validResourceIds = new Set(data.resources.map(resource => resource.resourceId));
const state = {
  view: "overview",
  search: "",
  queue: "needs_context",
  selectedGroup: null,
  selectedCollection: null,
  selectedResource: null,
  format: "all",
  browser: "all",
  decisions: loadDecisions()
};

const app = document.getElementById("app");
app.innerHTML = `
  <header class="topbar">
    <div class="brand">
      <div class="brand-mark" aria-hidden="true">TA</div>
      <div><h1>TabAtlas</h1><p id="freshness"></p></div>
    </div>
    <label class="search-wrap">
      <span class="sr-only">Search tab library</span>
      <input id="search" class="search" type="search" placeholder="Search titles, summaries, sources, groups, and topics" aria-label="Search tab library">
    </label>
    <button id="exportDecisions" class="export-button" type="button" disabled>
      Export decisions <span id="decisionCount" class="button-count">0</span>
    </button>
  </header>
  <nav id="viewTabs" class="view-tabs" aria-label="Library views" role="tablist">
    <button type="button" role="tab" data-view="overview">Overview</button>
    <button type="button" role="tab" data-view="decide">Decide</button>
    <button type="button" role="tab" data-view="groups">Groups</button>
    <button type="button" role="tab" data-view="collections">Collections</button>
    <button type="button" role="tab" data-view="resources">Resources</button>
  </nav>
  <main id="screen" class="screen"></main>`;

const elements = {
  freshness: document.getElementById("freshness"),
  search: document.getElementById("search"),
  exportDecisions: document.getElementById("exportDecisions"),
  decisionCount: document.getElementById("decisionCount"),
  viewTabs: document.getElementById("viewTabs"),
  screen: document.getElementById("screen")
};

elements.search.addEventListener("input", () => {
  state.search = elements.search.value.trim().toLocaleLowerCase();
  render();
});

elements.exportDecisions.addEventListener("click", exportDecisions);
elements.viewTabs.addEventListener("click", event => {
  const button = event.target.closest("button[data-view]");
  if (!button) return;
  state.view = button.dataset.view;
  state.search = "";
  elements.search.value = "";
  state.selectedResource = null;
  if (state.view === "groups") state.selectedGroup = null;
  if (state.view === "collections") state.selectedCollection = null;
  renderAtTop();
});

renderFreshness();
render();

function render() {
  updateNavigation();
  updateDecisionExport();
  if (state.search) {
    renderSearch();
    return;
  }
  if (state.view === "decide") renderDecide();
  else if (state.view === "groups") renderGroups();
  else if (state.view === "collections") renderCollections();
  else if (state.view === "resources") renderResources();
  else renderOverview();
}

function renderFreshness() {
  const captures = data.inventory.captures || [];
  elements.freshness.textContent = captures.length
    ? captures.map(item => `${capitalize(item.browser)} ${formatDate(item.capturedAt)}`).join(" / ")
    : "No capture imported yet";
}

function updateNavigation() {
  for (const button of elements.viewTabs.querySelectorAll("button[data-view]")) {
    const selected = !state.search && button.dataset.view === state.view;
    button.classList.toggle("active", selected);
    button.setAttribute("aria-selected", String(selected));
  }
}

function updateDecisionExport() {
  const count = Object.keys(state.decisions).length;
  elements.decisionCount.textContent = String(count);
  elements.exportDecisions.disabled = count === 0;
}

function renderOverview() {
  const fragment = document.createDocumentFragment();
  const intro = node("section", "overview-intro");
  const introCopy = node("div", "overview-copy");
  introCopy.append(
    node("p", "eyebrow", "Current library"),
    node("h2", "", `${formatNumber(data.inventory.currentResources || 0)} resources to decide, not ${formatNumber(data.inventory.currentTabs || 0)} raw tabs`),
    node("p", "lede", "Start with uncertain items, repeated copies, or an existing browser group. Open the full resource list only when you need it.")
  );
  const captureSummary = node("dl", "capture-summary");
  captureSummary.append(
    summaryFact("Unique resources", data.inventory.currentResources || 0),
    summaryFact("Open copies", data.inventory.currentTabs || 0),
    summaryFact("Browser groups", data.inventory.groups || 0)
  );
  intro.append(introCopy, captureSummary);
  fragment.append(intro);

  const decisionSection = node("section", "section-block");
  decisionSection.append(sectionHeading("Decide next", "The smallest queues with the clearest payoff."));
  const decisionGrid = node("div", "decision-grid");
  const queueCards = [
    ["needs_context", "Needs context", countResources(resource => !resource.collections.length), "A topic has not been established."],
    ["duplicate", "Multiple copies", countResources(resource => resource.tabs.length > 1), "One decision can remove repeated tabs later."],
    ["ungrouped", "Loose tabs", countResources(resource => !resource.presentation.groupTitles.length), "Not protected by a browser group."],
    ["queued", "Queued decisions", Object.keys(state.decisions).length, "Already marked in this report."]
  ];
  for (const [id, label, count, description] of queueCards) {
    decisionGrid.append(directoryButton("decision-card", label, count, description, () => openDecision(id)));
  }
  decisionSection.append(decisionGrid);
  fragment.append(decisionSection);

  const groupSection = node("section", "section-block");
  groupSection.append(sectionHeading("Largest browser groups", "Existing groups often contain the user's strongest intent.", "All groups", () => setView("groups")));
  const groupGrid = node("div", "directory-grid");
  for (const group of (data.groups || []).slice(0, 6)) groupGrid.append(groupCard(group));
  groupSection.append(groupGrid);
  fragment.append(groupSection);

  const collectionSection = node("section", "section-block");
  collectionSection.append(sectionHeading("Topic collections", "Broad subjects, separated from format and next-step intent.", "All collections", () => setView("collections")));
  const collectionGrid = node("div", "directory-grid");
  for (const collection of (data.collectionSummaries || []).slice(0, 8)) collectionGrid.append(collectionCard(collection));
  collectionSection.append(collectionGrid);
  fragment.append(collectionSection);

  const formatSection = node("section", "format-strip", "");
  formatSection.append(node("h2", "", "Library mix"));
  const formatList = node("div", "format-list");
  for (const item of (data.facets.formats || []).slice(0, 8)) {
    const format = node("button", "format-stat");
    format.type = "button";
    format.append(node("strong", "", formatNumber(item.count)), node("span", "", item.name));
    format.addEventListener("click", () => {
      state.view = "resources";
      state.format = item.name;
      state.selectedResource = null;
      renderAtTop();
    });
    formatList.append(format);
  }
  formatSection.append(formatList);
  fragment.append(formatSection);
  elements.screen.replaceChildren(fragment);
}

function renderDecide() {
  const queues = [
    ["needs_context", "Needs context", countResources(resource => !resource.collections.length)],
    ["duplicate", "Multiple copies", countResources(resource => resource.tabs.length > 1)],
    ["ungrouped", "Loose tabs", countResources(resource => !resource.presentation.groupTitles.length)],
    ["queued", "Queued", Object.keys(state.decisions).length]
  ];
  const controls = node("div", "segmented", "");
  controls.setAttribute("aria-label", "Decision queue");
  for (const [id, label, count] of queues) {
    const button = node("button", state.queue === id ? "active" : "");
    button.type = "button";
    button.setAttribute("aria-pressed", String(state.queue === id));
    button.append(node("span", "", label), node("span", "segment-count", String(count)));
    button.addEventListener("click", () => {
      state.queue = id;
      state.selectedResource = null;
      renderAtTop();
    });
    controls.append(button);
  }
  const resources = resourcesForQueue(state.queue);
  const descriptions = {
    needs_context: "These resources have a description but no trustworthy topic assignment.",
    duplicate: "These resources have more than one live tab instance.",
    ungrouped: "These resources are not currently protected by a named browser group.",
    queued: "These decisions are stored locally and can be exported for Codex to apply."
  };
  const workspace = resourceWorkspace(resources, {
    eyebrow: "Decision queue",
    title: queues.find(item => item[0] === state.queue)[1],
    summary: descriptions[state.queue],
    controls
  });
  elements.screen.replaceChildren(workspace);
}

function renderGroups() {
  const groups = data.groups || [];
  const selected = groups.find(group => group.id === state.selectedGroup);
  if (selected) {
    const resources = orderedResources(selected.resourceIds);
    elements.screen.replaceChildren(resourceWorkspace(resources, {
      eyebrow: `${capitalize(selected.browser)} browser group`,
      title: selected.displayTitle,
      summary: selected.summary,
      backLabel: "All groups",
      onBack: () => {
        state.selectedGroup = null;
        state.selectedResource = null;
        renderAtTop();
      },
      facts: [
        `${selected.tabCount} tabs`,
        `${selected.resourceCount} resource${selected.resourceCount === 1 ? "" : "s"}`,
        selected.collapsed ? "Collapsed in browser" : "Expanded in browser"
      ]
    }));
    return;
  }
  const page = node("section", "directory-page");
  page.append(pageHeading("Browser groups", `${groups.length} captured groups. Each opens independently; no hidden collection or browser filter is carried across.`));
  const grid = node("div", "directory-grid wide");
  for (const group of groups) grid.append(groupCard(group));
  page.append(grid);
  elements.screen.replaceChildren(page);
}

function renderCollections() {
  const collections = data.collectionSummaries || [];
  const selected = collections.find(collection => collection.id === state.selectedCollection);
  if (selected) {
    elements.screen.replaceChildren(resourceWorkspace(orderedResources(selected.resourceIds), {
      eyebrow: "Topic collection",
      title: selected.name,
      summary: selected.description,
      backLabel: "All collections",
      onBack: () => {
        state.selectedCollection = null;
        state.selectedResource = null;
        renderAtTop();
      },
      facts: [
        `${selected.resourceCount} resource${selected.resourceCount === 1 ? "" : "s"}`,
        selected.topFormats[0] ? `${selected.topFormats[0].count} ${selected.topFormats[0].name.toLocaleLowerCase()}` : "Mixed formats",
        selected.topIntents[0] ? `Common intent: ${selected.topIntents[0].name}` : "No dominant intent"
      ]
    }));
    return;
  }
  const page = node("section", "directory-page");
  page.append(pageHeading("Topic collections", `${collections.length} broad subjects. Format and next-step intent remain separate signals.`));
  const grid = node("div", "directory-grid wide");
  for (const collection of collections) grid.append(collectionCard(collection));
  page.append(grid);
  elements.screen.replaceChildren(page);
}

function renderResources() {
  const controls = node("div", "resource-controls");
  controls.append(
    selectControl("Format", ["all", ...(data.facets.formats || []).map(item => item.name)], state.format, value => {
      state.format = value;
      state.selectedResource = null;
      render();
    }),
    selectControl("Browser", ["all", "chrome", "edge"], state.browser, value => {
      state.browser = value;
      state.selectedResource = null;
      render();
    })
  );
  const resources = data.resources.filter(resource => {
    if (state.format !== "all" && resource.presentation.format !== state.format) return false;
    if (state.browser !== "all" && !resource.tabs.some(tab => tab.browser === state.browser)) return false;
    return true;
  });
  elements.screen.replaceChildren(resourceWorkspace(resources, {
    eyebrow: "Full library",
    title: "Resources",
    summary: "Use this view for lookup. Decision queues, browser groups, and topic collections provide faster starting points.",
    controls
  }));
}

function renderSearch() {
  const resources = data.resources.filter(matchesSearch);
  elements.screen.replaceChildren(resourceWorkspace(resources, {
    eyebrow: "Global search",
    title: "Search results",
    summary: `${resources.length} match${resources.length === 1 ? "" : "es"} for \"${elements.search.value.trim()}\".`
  }));
}

function resourceWorkspace(resources, options) {
  const wrapper = node("section", "resource-page");
  const head = node("header", "scope-head");
  const copy = node("div", "scope-copy");
  if (options.backLabel) {
    const back = node("button", "back-button", options.backLabel);
    back.type = "button";
    back.addEventListener("click", options.onBack);
    copy.append(back);
  }
  copy.append(node("p", "eyebrow", options.eyebrow || "Library"), node("h2", "", options.title), node("p", "scope-summary", options.summary || ""));
  head.append(copy);
  if (options.facts && options.facts.length) {
    const facts = node("div", "scope-facts");
    for (const fact of options.facts) facts.append(node("span", "", fact));
    head.append(facts);
  }
  wrapper.append(head);
  if (options.controls) wrapper.append(options.controls);

  const workspace = node("div", "resource-workspace");
  const listPane = node("section", "list-pane");
  const listHead = node("div", "list-head");
  listHead.append(node("strong", "", `${formatNumber(resources.length)} resource${resources.length === 1 ? "" : "s"}`));
  listPane.append(listHead);
  const list = node("div", "resource-list");
  const inspector = node("aside", "inspector");
  inspector.setAttribute("aria-label", "Resource details");
  inspector.setAttribute("aria-live", "polite");
  inspector.tabIndex = -1;

  if (!resources.length) {
    list.append(node("div", "empty-state", "No resources in this view."));
    renderInspector(inspector, null);
  } else {
    if (!resources.some(resource => resource.resourceId === state.selectedResource)) {
      state.selectedResource = resources[0].resourceId;
    }
    for (const resource of resources) list.append(resourceRow(resource, list, inspector));
    renderInspector(inspector, resources.find(resource => resource.resourceId === state.selectedResource) || resources[0]);
  }
  listPane.append(list);
  workspace.append(listPane, inspector);
  wrapper.append(workspace);
  return wrapper;
}

function resourceRow(resource, list, inspector) {
  const presentation = resource.presentation;
  const button = node("button", `resource-row ${resource.resourceId === state.selectedResource ? "selected" : ""}`);
  button.type = "button";
  button.dataset.resourceId = resource.resourceId;
  button.setAttribute("aria-pressed", String(resource.resourceId === state.selectedResource));
  button.append(previewTile(presentation, "small"));

  const identity = node("div", "row-identity");
  identity.append(
    node("p", "resource-title", resource.title || "Untitled resource"),
    node("p", "resource-source", `${presentation.source} / ${presentation.format}`)
  );
  const summary = node("div", "row-summary");
  summary.append(node("p", "brief", resource.brief || "No concise description yet."), node("p", "decision-cue", presentation.decisionCue));
  const meta = node("div", "row-signals");
  const queued = state.decisions[resource.resourceId];
  meta.append(
    signalBadge(queued ? queuedLabel(queued) : presentation.decisionLabel, queued ? `queued ${queued}` : presentation.decisionTone),
    node("span", "intent", presentation.intent),
    node("span", "copy-count", `${resource.tabs.length} tab${resource.tabs.length === 1 ? "" : "s"}`)
  );
  button.append(identity, summary, meta);
  button.addEventListener("click", () => {
    state.selectedResource = resource.resourceId;
    for (const row of list.querySelectorAll(".resource-row")) {
      const selected = row.dataset.resourceId === resource.resourceId;
      row.classList.toggle("selected", selected);
      row.setAttribute("aria-pressed", String(selected));
    }
    renderInspector(inspector, resource);
    inspector.focus({ preventScroll: true });
    if (window.matchMedia("(max-width: 860px)").matches) inspector.scrollIntoView({ behavior: "smooth", block: "start" });
  });
  return button;
}

function renderInspector(inspector, resource) {
  if (!resource) {
    inspector.replaceChildren(node("div", "inspector-empty", "Select a resource to inspect it."));
    return;
  }
  const presentation = resource.presentation;
  const fragment = document.createDocumentFragment();
  const preview = node("div", `inspector-preview accent-${presentation.preview.accent}`);
  const generated = previewTile(presentation, "large");
  preview.append(generated);
  if (presentation.preview.remoteImage && presentation.preview.requiresUserLoad) {
    const load = node("button", "load-preview", "Load source preview");
    load.type = "button";
    load.title = "Loads this public preview from the source only when requested.";
    load.addEventListener("click", () => {
      const image = document.createElement("img");
      image.className = "preview-image";
      image.alt = `Preview for ${resource.title || "resource"}`;
      image.referrerPolicy = "no-referrer";
      image.addEventListener("error", () => preview.replaceChildren(generated));
      image.src = presentation.preview.remoteImage;
      preview.replaceChildren(image);
    });
    preview.append(load);
  }
  fragment.append(preview);

  const header = node("header", "inspector-head");
  header.append(node("p", "eyebrow", `${presentation.source} / ${presentation.format}`), node("h2", "", resource.title || "Untitled resource"));
  const openUrl = resource.openUrl || resource.canonicalUrl;
  if (/^(https?|file):/i.test(openUrl)) {
    const link = node("a", "open-link", "Open source");
    link.href = openUrl;
    link.target = "_blank";
    link.rel = "noreferrer";
    header.append(link);
  }
  fragment.append(header);

  const glance = node("section", "glance");
  glance.append(node("h3", "", "At a glance"), node("p", "glance-brief", resource.brief || "No concise description yet."));
  const cues = node("dl", "cue-list");
  cues.append(cue("Context", presentation.contextCue), cue("Next", presentation.decisionCue));
  glance.append(cues);
  fragment.append(glance);

  const decisions = node("section", "decision-panel");
  decisions.append(node("h3", "", "Decision"));
  const actions = node("div", "decision-actions");
  const selectedDecision = state.decisions[resource.resourceId] || "";
  for (const [status, label] of [["saved", "Keep"], ["open", "Later"], ["close_candidate", "Close candidate"]]) {
    const action = node("button", selectedDecision === status ? "active" : "", label);
    action.type = "button";
    action.setAttribute("aria-pressed", String(selectedDecision === status));
    action.title = "Queues a decision only; browser tabs are unchanged.";
    action.addEventListener("click", () => setDecision(resource.resourceId, status));
    actions.append(action);
  }
  decisions.append(actions);
  fragment.append(decisions);

  if (resource.collections.length) {
    const topics = node("section", "inspector-section");
    topics.append(node("h3", "", "Topics"));
    const badges = node("div", "topic-list");
    for (const collection of resource.collections) badges.append(node("span", collection.accepted ? "topic accepted" : "topic", collection.name));
    topics.append(badges);
    fragment.append(topics);
  }

  const metadata = node("section", "inspector-section");
  metadata.append(node("h3", "", "Metadata"));
  const facts = node("dl", "metadata-grid");
  facts.append(
    cue("Source", presentation.source),
    cue("Format", presentation.format),
    cue("Intent", presentation.intent),
    cue("Open copies", String(resource.tabs.length)),
    cue("Browsers", [...new Set(resource.tabs.map(tab => capitalize(tab.browser)))].join(", ")),
    cue("First seen", formatDate(resource.firstSeenAt, true))
  );
  metadata.append(facts);
  fragment.append(metadata);

  if (resource.detail) {
    const details = document.createElement("details");
    details.className = "more-details";
    details.append(node("summary", "", "More context"), node("p", "", resource.detail));
    fragment.append(details);
  }

  const instances = document.createElement("details");
  instances.className = "more-details";
  instances.append(node("summary", "", `Tab instances (${resource.tabs.length})`));
  const instanceList = node("div", "instance-list");
  for (const tab of resource.tabs) {
    const item = node("div", "instance-item");
    item.append(
      node("strong", "", `${capitalize(tab.browser)}${tab.groupTitle ? ` / ${tab.groupTitle}` : ""}`),
      node("span", "", `Position ${Number(tab.position) + 1}${tab.pinned ? " / pinned" : ""}${tab.active ? " / active" : ""}`)
    );
    instanceList.append(item);
  }
  instances.append(instanceList);
  fragment.append(instances);
  inspector.replaceChildren(fragment);
}

function groupCard(group) {
  const card = node("button", "directory-card group-card");
  card.type = "button";
  card.dataset.groupId = group.id;
  const top = node("div", "card-top");
  top.append(colorSwatch(group.color), node("span", "card-kicker", `${capitalize(group.browser)} group`), node("span", "card-count", `${group.resourceCount} resource${group.resourceCount === 1 ? "" : "s"}`));
  card.append(top, node("h3", "", group.displayTitle), node("p", "card-summary", group.summary));
  const tags = node("div", "card-tags");
  const values = group.topCollections.length ? group.topCollections.slice(0, 2) : group.topFormats.slice(0, 2).map(item => item.name);
  for (const value of values) tags.append(node("span", "", value));
  card.append(tags);
  card.addEventListener("click", () => {
    state.view = "groups";
    state.selectedGroup = group.id;
    state.selectedResource = null;
    renderAtTop();
  });
  return card;
}

function collectionCard(collection) {
  const card = node("button", "directory-card collection-card");
  card.type = "button";
  card.dataset.collectionId = collection.id;
  const top = node("div", "card-top");
  top.append(node("span", "collection-mark", initials(collection.name)), node("span", "card-kicker", "Topic"), node("span", "card-count", `${collection.resourceCount} resource${collection.resourceCount === 1 ? "" : "s"}`));
  card.append(top, node("h3", "", collection.name), node("p", "card-summary", collection.description));
  const tags = node("div", "card-tags");
  for (const item of collection.topFormats.slice(0, 2)) tags.append(node("span", "", `${item.count} ${item.name.toLocaleLowerCase()}`));
  card.append(tags);
  card.addEventListener("click", () => {
    state.view = "collections";
    state.selectedCollection = collection.id;
    state.selectedResource = null;
    renderAtTop();
  });
  return card;
}

function directoryButton(className, label, count, description, onClick) {
  const button = node("button", className);
  button.type = "button";
  button.append(node("strong", "", formatNumber(count)), node("span", "decision-label", label), node("p", "", description));
  button.addEventListener("click", onClick);
  return button;
}

function pageHeading(title, summary) {
  const header = node("header", "page-heading");
  header.append(node("p", "eyebrow", "Library structure"), node("h2", "", title), node("p", "", summary));
  return header;
}

function sectionHeading(title, summary, actionLabel, onAction) {
  const header = node("header", "section-heading");
  const copy = node("div");
  copy.append(node("h2", "", title), node("p", "", summary));
  header.append(copy);
  if (actionLabel) {
    const action = node("button", "text-button", actionLabel);
    action.type = "button";
    action.addEventListener("click", onAction);
    header.append(action);
  }
  return header;
}

function selectControl(label, values, selected, onChange) {
  const wrapper = node("label", "select-control");
  wrapper.append(node("span", "", label));
  const select = document.createElement("select");
  for (const value of values) {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = value === "all" ? `All ${label.toLocaleLowerCase()}s` : capitalize(value);
    option.selected = value === selected;
    select.append(option);
  }
  select.addEventListener("change", () => onChange(select.value));
  wrapper.append(select);
  return wrapper;
}

function previewTile(presentation, size) {
  const tile = node("div", `preview-tile ${size} accent-${presentation.preview.accent}`);
  tile.setAttribute("aria-hidden", "true");
  tile.append(node("strong", "", presentation.preview.label), node("span", "", presentation.format));
  return tile;
}

function signalBadge(label, tone) {
  return node("span", `signal tone-${String(tone).replaceAll("_", "-")}`, label);
}

function colorSwatch(color) {
  const swatch = node("span", `group-swatch group-${String(color).toLocaleLowerCase()}`);
  swatch.setAttribute("aria-hidden", "true");
  return swatch;
}

function summaryFact(label, value) {
  const wrapper = node("div", "summary-fact");
  wrapper.append(node("dd", "", formatNumber(value)), node("dt", "", label));
  return wrapper;
}

function cue(label, value) {
  const wrapper = node("div", "cue");
  wrapper.append(node("dt", "", label), node("dd", "", value || "Not recorded"));
  return wrapper;
}

function resourcesForQueue(queue) {
  if (queue === "needs_context") return data.resources.filter(resource => !resource.collections.length);
  if (queue === "duplicate") return data.resources.filter(resource => resource.tabs.length > 1);
  if (queue === "ungrouped") return data.resources.filter(resource => !resource.presentation.groupTitles.length);
  return data.resources.filter(resource => state.decisions[resource.resourceId]);
}

function orderedResources(ids) {
  const byId = new Map(data.resources.map(resource => [resource.resourceId, resource]));
  return ids.map(id => byId.get(id)).filter(Boolean);
}

function matchesSearch(resource) {
  const value = [
    resource.title,
    resource.displayUrl,
    resource.brief,
    resource.detail,
    resource.whyKept,
    resource.nextAction,
    resource.presentation.source,
    resource.presentation.format,
    resource.presentation.intent,
    resource.presentation.contextCue,
    ...resource.presentation.groupTitles,
    ...resource.collections.map(collection => collection.name)
  ].join("\n").toLocaleLowerCase();
  return value.includes(state.search);
}

function openDecision(queue) {
  state.view = "decide";
  state.queue = queue;
  state.selectedResource = null;
  renderAtTop();
}

function setView(view) {
  state.view = view;
  state.selectedResource = null;
  if (view === "groups") state.selectedGroup = null;
  if (view === "collections") state.selectedCollection = null;
  renderAtTop();
}

function renderAtTop() {
  render();
  window.scrollTo({ top: 0 });
}

function setDecision(resourceId, status) {
  if (state.decisions[resourceId] === status) delete state.decisions[resourceId];
  else state.decisions[resourceId] = status;
  saveDecisions();
  const scrollY = window.scrollY;
  render();
  window.scrollTo({ top: scrollY });
}

function loadDecisions() {
  try {
    const parsed = JSON.parse(localStorage.getItem(STORAGE_KEY) || "{}");
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return {};
    return Object.fromEntries(Object.entries(parsed).filter(([resourceId, status]) =>
      validResourceIds.has(resourceId) && ["saved", "open", "close_candidate"].includes(status)
    ));
  } catch {
    return {};
  }
}

function saveDecisions() {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(state.decisions));
  } catch {
    // The report remains usable when browser storage is unavailable.
  }
}

function exportDecisions() {
  const resources = Object.entries(state.decisions).map(([resourceId, status]) => ({ resourceId, status }));
  if (!resources.length) return;
  const payload = {
    schemaVersion: 1,
    generatedAt: new Date().toISOString(),
    sourceReportGeneratedAt: data.generatedAt,
    resources
  };
  const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = "tabatlas-decisions.json";
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function queuedLabel(status) {
  return { saved: "Keep", open: "Later", close_candidate: "Close candidate" }[status] || "Queued";
}

function countResources(predicate) {
  return data.resources.filter(predicate).length;
}

function node(tag, className = "", text = "") {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== "") element.textContent = text;
  return element;
}

function initials(value) {
  const parts = String(value || "?").split(/[.\-_\s]+/).filter(Boolean);
  return parts.slice(0, 2).map(part => part[0]).join("").toUpperCase() || "?";
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
