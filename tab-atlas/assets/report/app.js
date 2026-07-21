const data = window.__TAB_ATLAS__ || {
  inventory: {},
  resources: [],
  discoveries: [],
  groups: [],
  collectionSummaries: [],
  spaceSummaries: [],
  topicSummaries: [],
  focusSummaries: [],
  projectSummaries: [],
  actionListSummaries: [],
  sourceSummaries: [],
  facets: {},
  workspace: {}
};

const PAGE_SIZE = 30;
const REQUEST_SAFETY_TEXT = "Downloads a request only. Codex verifies a fresh capture, a recoverable backup, and the post-close state before execution.";
const hasSpaceContract = Object.prototype.hasOwnProperty.call(data, "spaceSummaries");
const resources = Array.isArray(data.resources) ? data.resources : [];
const discoveries = Array.isArray(data.discoveries) ? data.discoveries : [];
const allResources = [...discoveries, ...resources];
const resourceById = new Map(allResources.map(resource => [resource.resourceId, resource]));
let spaceSummaries = hasSpaceContract
  ? (data.spaceSummaries || [])
  : (data.collectionSummaries || []);
let projectSummaries = data.projectSummaries || [];
let actionListSummaries = data.actionListSummaries || [];
const sourceSummaries = Array.isArray(data.sourceSummaries) ? data.sourceSummaries : [];
const galleryLoaders = new WeakMap();
const galleryObservers = new Set();
const galleryLoadTimers = new Set();
const motionLoadTimers = new Set();
const connectionHints = new Set();
let activeMotionPreview = null;

const workspace = {
  interactive: false,
  csrfToken: "",
  agent: {},
  noteCache: new Map(),
  noteLoads: new Map(),
  notePolls: new Map(),
  requestPolls: new Map(),
  latestAuditByResource: new Map(),
  messages: [],
  recording: null,
  previousView: null
};

const state = {
  view: "home",
  libraryLens: "purpose",
  search: "",
  scope: null,
  sourceScope: null,
  sourcePublisher: "all",
  sourceTopic: "all",
  topic: "all",
  focus: "all",
  reviewMode: discoveries.length ? "discoveries" : "inbox",
  filters: defaultFilters(),
  visibleLimit: PAGE_SIZE,
  selectedResource: null,
  lastFocus: null,
  agentOpen: false
};

const app = document.getElementById("app");
app.innerHTML = `
  <header class="topbar">
    <button id="homeBrand" class="brand" type="button" aria-label="Open TabAtlas home">
      <span class="brand-mark" aria-hidden="true">TA</span>
      <span class="brand-copy"><strong>TabAtlas</strong><small id="freshness"></small></span>
    </button>
    <label class="search-wrap">
      <span class="sr-only">Search tab library</span>
      <input id="search" class="search" type="search" placeholder="Search titles, summaries, spaces, topics, and sources" aria-label="Search tab library">
    </label>
  </header>
  <nav id="primaryNav" class="primary-nav" aria-label="Primary views" role="tablist">
    <button type="button" role="tab" data-view="home">Home</button>
    <button type="button" role="tab" data-view="library">Library</button>
    <button type="button" role="tab" data-view="review">Review <span id="navLiveCount" class="nav-count" hidden>0</span></button>
  </nav>
  <main id="screen" class="screen"></main>
  <div id="drawerHost"></div>
  <button id="agentToggle" class="agent-toggle" type="button" aria-expanded="false" aria-controls="agentPanel" hidden>Ask Codex <span id="agentPending" class="agent-pending" hidden>0</span></button>
  <aside id="agentPanel" class="agent-panel" aria-label="TabAtlas Codex assistant" hidden>
    <header class="agent-panel-head">
      <div><strong>Codex</strong><span id="agentStatus">On demand</span></div>
      <button id="agentClose" class="agent-close" type="button" aria-label="Close Codex panel">&times;</button>
    </header>
    <p id="agentScope" class="agent-scope"></p>
    <div id="agentMessages" class="agent-messages" aria-live="polite"></div>
    <form id="agentForm" class="agent-form">
      <label class="sr-only" for="agentInput">Ask Codex about this library</label>
      <textarea id="agentInput" rows="3" maxlength="32768" placeholder="Ask about this resource or library"></textarea>
      <div class="agent-form-actions">
        <button id="agentOwnership" class="secondary-command" type="button">Open in Codex</button>
        <button class="primary-command" type="submit">Send</button>
      </div>
    </form>
  </aside>
  <div id="actionStatus" class="sr-only" role="status" aria-live="polite" aria-atomic="true"></div>`;

const elements = {
  topbar: document.querySelector(".topbar"),
  homeBrand: document.getElementById("homeBrand"),
  freshness: document.getElementById("freshness"),
  search: document.getElementById("search"),
  primaryNav: document.getElementById("primaryNav"),
  navLiveCount: document.getElementById("navLiveCount"),
  screen: document.getElementById("screen"),
  drawerHost: document.getElementById("drawerHost"),
  agentToggle: document.getElementById("agentToggle"),
  agentPending: document.getElementById("agentPending"),
  agentPanel: document.getElementById("agentPanel"),
  agentClose: document.getElementById("agentClose"),
  agentStatus: document.getElementById("agentStatus"),
  agentScope: document.getElementById("agentScope"),
  agentMessages: document.getElementById("agentMessages"),
  agentForm: document.getElementById("agentForm"),
  agentInput: document.getElementById("agentInput"),
  agentOwnership: document.getElementById("agentOwnership"),
  actionStatus: document.getElementById("actionStatus")
};

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

renderFreshness();
render();
initializeWorkspace();

function render() {
  stopActiveMotionPreview();
  clearMotionLoadTimers();
  disconnectGalleryObservers();
  updateNavigation();
  if (state.search) renderSearch();
  else if (state.view === "spaces") renderSpaces();
  else if (state.view === "sources") renderSources();
  else if (state.view === "review") renderReview();
  else renderHome();
  renderDrawer();
  renderAgentPanel();
  connectGalleryObservers();
}

function clearMotionLoadTimers() {
  for (const timer of motionLoadTimers) window.clearTimeout(timer);
  motionLoadTimers.clear();
}

function disconnectGalleryObservers() {
  for (const observer of galleryObservers) observer.disconnect();
  galleryObservers.clear();
  for (const timer of galleryLoadTimers) window.clearTimeout(timer);
  galleryLoadTimers.clear();
}

function connectGalleryObservers() {
  if (typeof window.IntersectionObserver !== "function") return;
  for (const sentinel of elements.screen.querySelectorAll("[data-gallery-sentinel]")) {
    const loadNextBatch = galleryLoaders.get(sentinel);
    if (!loadNextBatch) continue;
    const observer = new IntersectionObserver(entries => {
      if (!entries.some(entry => entry.isIntersecting)) return;
      observer.unobserve(sentinel);
      sentinel.classList.add("is-loading");
      sentinel.setAttribute("aria-busy", "true");
      const status = sentinel.querySelector(".gallery-sentinel-status");
      const batchSize = Number(sentinel.dataset.nextBatchSize) || PAGE_SIZE;
      if (status) status.textContent = `Loading ${formatNumber(batchSize)} more resources`;

      const timer = window.setTimeout(() => {
        galleryLoadTimers.delete(timer);
        if (!galleryObservers.has(observer) || !sentinel.isConnected) return;
        if (loadNextBatch()) {
          observer.observe(sentinel);
        } else {
          observer.disconnect();
          galleryObservers.delete(observer);
        }
      }, 0);
      galleryLoadTimers.add(timer);
    }, { root: null, rootMargin: "720px 0px", threshold: 0 });
    galleryObservers.add(observer);
    observer.observe(sentinel);
  }
}

function renderFreshness() {
  const captures = data.inventory?.captures || [];
  elements.freshness.textContent = captures.length
    ? captures.map(item => `${capitalize(item.browser)} ${formatDate(item.capturedAt)}`).join(" / ")
    : "No capture imported yet";
}

function updateNavigation() {
  for (const button of elements.primaryNav.querySelectorAll("button[data-view]")) {
    const selected = !state.search && (
      button.dataset.view === state.view
      || (button.dataset.view === "library" && ["spaces", "sources"].includes(state.view))
    );
    button.classList.toggle("active", selected);
    button.setAttribute("aria-selected", String(selected));
  }
  const attentionCount = pendingDiscoveryCount() || currentTabCount();
  elements.navLiveCount.textContent = String(attentionCount);
  elements.navLiveCount.hidden = attentionCount === 0;
}

function renderHome() {
  const fragment = document.createDocumentFragment();
  const inbox = inboxResources();
  const safeDuplicates = safeDuplicateCount();
  const openTabs = currentTabCount();
  const pendingDiscoveries = pendingDiscoveryCount();
  const liveResources = currentResourceCount();
  const libraryResources = libraryResourceCount();
  const purposeSpaces = spaceSummaries.slice(0, 6);

  const intro = node("section", "home-head");
  const introCopy = node("div", "home-copy");
  introCopy.append(
    node("p", "eyebrow", "Durable library"),
    node("h2", "", "Browse what was captured, then clear the browser"),
    node("p", "scope-summary", openTabs
      ? `${formatNumber(libraryResources)} library resources; ${formatNumber(liveResources)} are represented by ${formatNumber(openTabs)} open tabs.`
      : `${formatNumber(libraryResources)} library resources. No captured tabs are currently open.`)
  );
  intro.append(introCopy, continueButton(pendingDiscoveries, inbox.length, safeDuplicates, openTabs));
  fragment.append(intro);

  const attention = node("section", "attention-section");
  attention.append(sectionHeading("Library status", "Organize uncertain resources or clear browser state already retained here."));
  const attentionRow = node("div", "attention-row");
  attentionRow.append(
    attentionButton("New discoveries", pendingDiscoveries, "Awaiting library review", () => openReview("discoveries")),
    attentionButton("Inbox", inbox.length, "No purpose space", () => openReview("inbox")),
    attentionButton("Safe exact duplicates", safeDuplicates, "Closeable extras", () => openReview("duplicates")),
    attentionButton("Open tabs", openTabs, `${formatNumber(liveResources)} live resources`, () => openReview("open"))
  );
  attention.append(attentionRow);
  fragment.append(attention);

  if (actionListSummaries.length) {
    const actionLists = node("section", "section-block action-lists-section");
    actionLists.append(sectionHeading("Action lists", "Resources with an explicit follow-through state."));
    const list = node("div", "action-list-directory");
    for (const summary of actionListSummaries.slice(0, 6)) list.append(actionListRow(summary));
    actionLists.append(list);
    fragment.append(actionLists);
  }

  const spacesSection = node("section", "section-block");
  spacesSection.append(sectionHeading(
    "Purpose spaces",
    "Resources organized by how they support the user's work.",
    spaceSummaries.length > 6 ? "All spaces" : "",
    spaceSummaries.length > 6 ? () => setView("spaces") : null
  ));
  if (purposeSpaces.length) {
    const grid = node("div", "space-grid");
    for (const summary of purposeSpaces) grid.append(spaceCard(summary));
    spacesSection.append(grid);
  } else {
    spacesSection.append(emptyState("No purpose spaces have been assigned yet."));
  }
  fragment.append(spacesSection);

  const workspaces = node("section", "section-block active-workspaces");
  workspaces.append(sectionHeading("Active workspaces", "Project-specific material kept separate from broad interests."));
  if (projectSummaries.length) {
    const list = node("div", "workspace-list");
    for (const summary of projectSummaries) list.append(workspaceRow(summary));
    workspaces.append(list);
  } else {
    workspaces.append(node("p", "quiet-empty", "No active project workspaces in this capture."));
  }
  fragment.append(workspaces);
  elements.screen.replaceChildren(fragment);
}

function continueButton(discoveryCount, inboxCount, duplicateCount, openTabCount) {
  const button = node("button", "continue-action");
  button.type = "button";
  let count = openTabCount;
  let label = "Review open tabs";
  let detail = duplicateCount
    ? `${formatNumber(duplicateCount)} safe exact extras can be removed first, or request the full archive.`
    : "Captured resources remain in the library after their browser tabs close.";
  let onClick = () => openReview("open");
  if (discoveryCount) {
    count = discoveryCount;
    label = "Review new discoveries";
    detail = "Accept selected resources or add the complete batch to the durable library.";
    onClick = () => openReview("discoveries");
  } else if (!openTabCount && inboxCount) {
    count = inboxCount;
    label = "Work through Inbox";
    detail = "Give unplaced library resources a useful purpose space.";
    onClick = () => openReview("inbox");
  } else if (!openTabCount && !inboxCount) {
    count = libraryResourceCount();
    label = "Open purpose spaces";
    detail = "Browse the retained library by the work each resource supports.";
    onClick = () => setView("spaces");
  }
  button.append(
    node("span", "continue-kicker", "Continue"),
    node("strong", "", label),
    node("span", "continue-detail", detail),
    node("span", "continue-count", formatNumber(count))
  );
  button.addEventListener("click", onClick);
  return button;
}

function attentionButton(label, count, detail, onClick) {
  const button = node("button", "attention-button");
  button.type = "button";
  button.append(node("strong", "", formatNumber(count)), node("span", "", label), node("small", "", detail));
  button.addEventListener("click", onClick);
  return button;
}

function renderSpaces() {
  const summary = selectedScopeSummary();
  if (state.scope && summary) {
    renderScopeDetail(summary);
    return;
  }
  if (state.scope && !summary) state.scope = null;

  const page = node("section", "directory-page");
  page.append(libraryLensControl());
  page.append(pageHeading("Purpose spaces", "Open a space to see its topics and the resources most relevant to that kind of work."));
  if (!spaceSummaries.length) {
    page.append(emptyState("No purpose spaces have been assigned yet."));
  } else {
    const grid = node("div", "space-grid directory-spaces");
    for (const summaryItem of spaceSummaries) grid.append(spaceCard(summaryItem));
    page.append(grid);
  }
  elements.screen.replaceChildren(page);
}

function renderSources() {
  const summary = selectedSourceSummary();
  if (state.sourceScope && summary) {
    renderSourceDetail(summary);
    return;
  }
  if (state.sourceScope && !summary) state.sourceScope = null;

  const page = node("section", "directory-page sources-page");
  page.append(libraryLensControl());
  page.append(pageHeading(
    "Sources",
    "Browse the retained library by platform, site, owner, channel, community, and semantic topic."
  ));
  if (!sourceSummaries.length) {
    page.append(emptyState("No source information is available yet."));
  } else {
    const list = node("div", "source-directory");
    for (const summaryItem of sourceSummaries) list.append(sourceSummaryRow(summaryItem));
    page.append(list);
  }
  elements.screen.replaceChildren(page);
}

function sourceSummaryRow(summary) {
  const button = node("button", "source-summary-row");
  button.type = "button";
  const mosaic = node("span", "source-summary-mosaic");
  const previewIds = (summary.previewResourceIds || summary.resourceIds || []).slice(0, 3);
  for (const id of previewIds) {
    const resource = resourceById.get(id);
    if (resource) mosaic.append(previewVisual(resource, "mosaic", false));
  }
  while (mosaic.childElementCount < 3) mosaic.append(node("span", "mosaic-empty"));

  const copy = node("span", "source-summary-copy");
  copy.append(
    node("span", "source-summary-kicker", `${formatNumber(summary.resourceCount)} resources`),
    node("strong", "", summary.name)
  );
  const facets = node("span", "source-summary-facets");
  const repeatedPublishers = (summary.publishers || []).filter(item => item.resourceCount > 1).slice(0, 3);
  const facetValues = repeatedPublishers.length ? repeatedPublishers : (summary.topTopics || []).slice(0, 3);
  for (const item of facetValues) facets.append(chip(`${item.name} ${formatNumber(item.resourceCount || item.count)}`));
  if (facets.childElementCount) copy.append(facets);

  const coverage = node("span", "source-summary-coverage");
  coverage.append(
    node("strong", "", formatNumber(summary.previewCount)),
    node("span", "", "image previews"),
    node("small", "", `${formatNumber(summary.metadataPreviewCount)} metadata previews`)
  );
  button.append(mosaic, copy, coverage);
  button.addEventListener("click", () => openSourceScope(summary.id));
  return button;
}

function renderSourceDetail(summary) {
  const members = resourcesForSourceSummary(summary);
  const publishers = summary.publishers || [];
  const topics = scopeTopics(summary, members);
  const validPublishers = new Set(publishers.map(item => item.name));
  if (state.sourcePublisher !== "all"
      && state.sourcePublisher !== "__unattributed"
      && !validPublishers.has(state.sourcePublisher)) {
    state.sourcePublisher = "all";
  }
  if (state.sourceTopic !== "all" && !topics.includes(state.sourceTopic)) state.sourceTopic = "all";

  const page = node("section", "source-detail-page");
  const head = node("header", "scope-head");
  const copy = node("div", "scope-copy");
  const back = node("button", "back-button", "Back to library");
  back.type = "button";
  back.addEventListener("click", () => {
    state.sourceScope = null;
    state.sourcePublisher = "all";
    state.sourceTopic = "all";
    state.filters = defaultFilters();
    state.visibleLimit = PAGE_SIZE;
    state.selectedResource = null;
    renderAtTop();
  });
  copy.append(
    back,
    node("p", "eyebrow", "Source lens"),
    node("h2", "", summary.name),
    node("p", "scope-summary", `${formatNumber(members.length)} retained resources grouped without changing their purpose spaces.`)
  );
  const facts = node("div", "scope-facts");
  facts.append(
    factChip(`${formatNumber(summary.previewCount)} image previews`),
    factChip(`${formatNumber(summary.metadataPreviewCount)} metadata previews`)
  );
  head.append(copy, facts);
  page.append(head);

  const sourceControls = node("div", "source-controls");
  if (publishers.length || summary.attributedCount < summary.resourceCount) {
    sourceControls.append(sourceSelectControl(
      "Owner, channel, or site",
      ["all", ...(summary.attributedCount < summary.resourceCount ? ["__unattributed"] : []), ...publishers.map(item => item.name)],
      state.sourcePublisher,
      value => {
        state.sourcePublisher = value;
        state.visibleLimit = PAGE_SIZE;
        state.selectedResource = null;
        renderAtTop();
      },
      "All owners and sites"
    ));
  }
  if (topics.length) {
    sourceControls.append(sourceSelectControl(
      "Topic",
      ["all", ...topics],
      state.sourceTopic,
      value => {
        state.sourceTopic = value;
        state.visibleLimit = PAGE_SIZE;
        state.selectedResource = null;
        renderAtTop();
      },
      "All topics"
    ));
  }
  if (sourceControls.childElementCount) page.append(sourceControls);
  page.append(filterPanel(members));

  const filtered = filterResources(members).filter(resource => {
    const publisher = String(resource.presentation?.publisher || "");
    const publisherMatches = state.sourcePublisher === "all"
      || (state.sourcePublisher === "__unattributed" ? !publisher : publisher === state.sourcePublisher);
    return publisherMatches
      && (state.sourceTopic === "all" || resourceTopics(resource).includes(state.sourceTopic));
  });
  page.append(resourceGallery(filtered, {
    label: state.sourcePublisher !== "all"
      ? sourcePublisherLabel(state.sourcePublisher)
      : (state.sourceTopic !== "all" ? state.sourceTopic : "All resources"),
    empty: "No resources match these source and filter choices."
  }));
  elements.screen.replaceChildren(page);
}

function renderScopeDetail(summary) {
  const members = resourcesForSummary(summary);
  const topics = scopeTopics(summary, members);
  const returnsHome = state.scope.kind === "project" || state.scope.kind === "action_list";
  if (state.topic !== "all" && !topics.includes(state.topic)) {
    state.topic = "all";
    state.focus = "all";
  }
  const focuses = scopeFocuses(members, state.topic);
  if (state.focus !== "all" && !focuses.includes(state.focus)) state.focus = "all";

  const page = node("section", "scope-page");
  const head = node("header", "scope-head");
  const copy = node("div", "scope-copy");
  const back = node("button", "back-button", returnsHome ? "Back to home" : "Back to library");
  back.type = "button";
  back.addEventListener("click", () => {
    if (returnsHome) state.view = "home";
    state.scope = null;
    state.topic = "all";
    state.focus = "all";
    state.filters = defaultFilters();
    state.visibleLimit = PAGE_SIZE;
    state.selectedResource = null;
    renderAtTop();
  });
  copy.append(
    back,
    node("p", "eyebrow", state.scope.kind === "project" ? "Active workspace" : (state.scope.kind === "action_list" ? "Action list" : "Purpose space")),
    node("h2", "", summary.name),
    node("p", "scope-summary", summary.objective || summary.description || "Resources grouped around this purpose.")
  );
  const facts = node("div", "scope-facts");
  facts.append(
    factChip(`${formatNumber(members.length)} resources`),
    factChip(`${formatNumber(members.filter(resource => liveTabs(resource).length).length)} currently open`)
  );
  head.append(copy, facts);
  page.append(head);

  if (topics.length) page.append(topicControl(topics));
  if (focuses.length) page.append(focusControl(focuses));
  page.append(filterPanel(members));

  const filtered = filterResources(members).filter(resource =>
    (state.topic === "all" || resourceTopics(resource).includes(state.topic))
    && (state.focus === "all" || resourceFocuses(resource, state.topic === "all" ? "" : state.topic).includes(state.focus))
  );
  page.append(resourceGallery(filtered, {
    label: state.focus !== "all" ? state.focus : (state.topic === "all" ? "All resources" : state.topic),
    empty: "No resources match these hierarchy and filter choices.",
    hideSpace: state.scope.kind === "space"
  }));
  elements.screen.replaceChildren(page);
}

function topicControl(topics) {
  const wrapper = node("div", "topic-control");
  wrapper.setAttribute("aria-label", "Topic filter");
  for (const topic of ["all", ...topics]) {
    const button = node("button", state.topic === topic ? "active" : "", topic === "all" ? "All topics" : topic);
    button.type = "button";
    button.setAttribute("aria-pressed", String(state.topic === topic));
    button.addEventListener("click", () => {
      state.topic = topic;
      state.focus = "all";
      state.visibleLimit = PAGE_SIZE;
      state.selectedResource = null;
      renderAtTop();
    });
    wrapper.append(button);
  }
  return wrapper;
}

function focusControl(focuses) {
  const wrapper = node("div", "topic-control focus-control");
  wrapper.setAttribute("aria-label", "Focus filter");
  for (const focus of ["all", ...focuses]) {
    const button = node("button", state.focus === focus ? "active" : "", focus === "all" ? "All focus areas" : focus);
    button.type = "button";
    button.setAttribute("aria-pressed", String(state.focus === focus));
    button.addEventListener("click", () => {
      state.focus = focus;
      state.visibleLimit = PAGE_SIZE;
      state.selectedResource = null;
      renderAtTop();
    });
    wrapper.append(button);
  }
  return wrapper;
}

function filterPanel(members) {
  const details = document.createElement("details");
  details.className = "filter-panel";
  if (activeFilterCount()) details.open = true;
  const summary = node("summary", "filter-summary");
  summary.append(node("span", "", "Filters"), node("span", "filter-count", activeFilterCount() ? String(activeFilterCount()) : "All"));
  details.append(summary);

  const controls = node("div", "filter-controls");
  const formats = unique(members.map(resource => resource.presentation?.format).filter(Boolean));
  const browsers = unique(members.flatMap(resourceContexts).map(context => context.browser).filter(Boolean));
  const groups = unique(members.flatMap(contextGroupTitles));
  controls.append(
    selectControl("Format", ["all", ...formats], state.filters.format, value => updateFilter("format", value)),
    selectControl("Browser", ["all", ...browsers], state.filters.browser, value => updateFilter("browser", value)),
    selectControl("Browser group", ["all", "__ungrouped", ...groups], state.filters.group, value => updateFilter("group", value))
  );
  const clear = node("button", "clear-filters", "Clear filters");
  clear.type = "button";
  clear.disabled = activeFilterCount() === 0;
  clear.addEventListener("click", () => {
    state.filters = defaultFilters();
    state.visibleLimit = PAGE_SIZE;
    render();
  });
  controls.append(clear);
  details.append(controls);
  return details;
}

function renderReview() {
  const openResources = openTabResources();
  const modes = [
    ["discoveries", "New discoveries", discoveries.length],
    ["inbox", "Inbox", inboxResources().length],
    ["duplicates", "Exact duplicates", exactDuplicateResources().length],
    ["open", "Open tabs", currentTabCount()]
  ];
  const page = node("section", "review-page");
  const head = node("header", "review-head");
  const copy = node("div");
  copy.append(
    node("p", "eyebrow", "Library review"),
    node("h2", "", modes.find(item => item[0] === state.reviewMode)?.[1] || "Review"),
    node("p", "scope-summary", reviewDescription(state.reviewMode))
  );
  head.append(copy);
  const requestPanel = reviewActionPanel(state.reviewMode);
  if (requestPanel) head.append(requestPanel);
  page.append(head);

  const control = node("div", "review-modes");
  control.setAttribute("aria-label", "Review mode");
  for (const [id, label, count] of modes) {
    const button = node("button", state.reviewMode === id ? "active" : "");
    button.type = "button";
    button.setAttribute("aria-pressed", String(state.reviewMode === id));
    button.append(node("span", "", label), node("span", "mode-count", formatNumber(count)));
    button.addEventListener("click", () => {
      state.reviewMode = id;
      state.visibleLimit = PAGE_SIZE;
      state.selectedResource = null;
      renderAtTop();
    });
    control.append(button);
  }
  page.append(control);

  let reviewResources = state.reviewMode === "discoveries" ? discoveries : inboxResources();
  if (state.reviewMode === "duplicates") reviewResources = exactDuplicateResources();
  if (state.reviewMode === "open") reviewResources = openResources;
  let galleryLabel = `${formatNumber(reviewResources.length)} resource${reviewResources.length === 1 ? "" : "s"}`;
  if (state.reviewMode === "discoveries") {
    galleryLabel = `${formatNumber(reviewResources.length)} awaiting approval`;
  } else if (state.reviewMode === "duplicates") {
    galleryLabel = `${formatNumber(safeDuplicateCount())} safe close candidate${safeDuplicateCount() === 1 ? "" : "s"}`;
  } else if (state.reviewMode === "open") {
    galleryLabel = `${formatNumber(currentTabCount())} open tab${currentTabCount() === 1 ? "" : "s"} across ${formatNumber(openResources.length)} resources`;
  }
  page.append(resourceGallery(reviewResources, {
    label: galleryLabel,
    empty: reviewEmptyMessage(state.reviewMode)
  }));
  elements.screen.replaceChildren(page);
}

function reviewActionPanel(mode) {
  if (!["discoveries", "duplicates", "open"].includes(mode)) return null;
  const duplicateCount = safeDuplicateCount();
  const openTabs = currentTabCount();
  const pending = pendingDiscoveryCount();
  const dismissedOpen = inventoryCount("currentDismissedResources", 0);
  const panel = node("div", "review-action");
  let label = openTabs ? "Close all captured tabs" : "All captured tabs closed";
  let disabled = openTabs === 0;
  if (mode === "discoveries") {
    label = pending ? "Accept all new discoveries" : "No new discoveries";
    disabled = pending === 0;
  } else if (mode === "duplicates") {
    label = duplicateCount ? "Close all duplicates" : "No safe duplicates";
    disabled = duplicateCount === 0;
  } else if (pending) {
    label = `Review ${formatNumber(pending)} new first`;
    disabled = true;
  } else if (dismissedOpen) {
    label = `Resolve ${formatNumber(dismissedOpen)} dismissed first`;
    disabled = true;
  }
  const button = node(
    "button",
    `primary-command ${mode === "discoveries" ? "accept-command" : "close-command"}`,
    label
  );
  const note = node(
    "p",
    "action-safety-note",
    mode === "discoveries"
      ? "Downloads a request to add this reviewed batch. Browser tabs are unchanged."
      : REQUEST_SAFETY_TEXT
  );
  note.id = "reviewActionSafety";
  button.type = "button";
  button.disabled = disabled;
  button.title = "Downloads a privacy-safe action request; it does not close tabs directly.";
  button.setAttribute("aria-describedby", note.id);
  button.addEventListener("click", () => {
    if (mode === "discoveries") requestDiscoveryAcceptance();
    else if (mode === "duplicates") requestDuplicateClose();
    else requestCapturedTabArchive();
  });
  panel.append(button, note);
  return panel;
}

function renderSearch() {
  const matches = allResources.filter(matchesSearch).sort(resourceSort);
  const page = node("section", "search-page");
  const head = node("header", "search-head");
  const copy = node("div");
  copy.append(
    node("p", "eyebrow", "Global search"),
    node("h2", "", "Search results"),
    node("p", "scope-summary", `${formatNumber(matches.length)} match${matches.length === 1 ? "" : "es"} for "${elements.search.value.trim()}".`)
  );
  const clear = node("button", "secondary-command", "Clear search");
  clear.type = "button";
  clear.addEventListener("click", clearSearch);
  head.append(copy, clear);
  page.append(head, resourceGallery(matches, {
    label: "Matching resources",
    empty: "No resources match this search."
  }));
  elements.screen.replaceChildren(page);
}

function resourceGallery(items, options = {}) {
  const section = node("section", "gallery-section");
  const heading = node("header", "gallery-head");
  const visibleCount = Math.min(items.length, state.visibleLimit);
  const count = node("span", "gallery-count", `${formatNumber(visibleCount)} of ${formatNumber(items.length)}`);
  heading.append(
    node("h3", "", options.label || "Resources"),
    count
  );
  section.append(heading);
  if (!items.length) {
    section.append(emptyState(options.empty || "No resources in this view."));
    return section;
  }

  const grid = node("div", "resource-grid");
  for (const resource of items.slice(0, visibleCount)) {
    grid.append(resourceCard(resource, options));
  }
  section.append(grid);
  if (visibleCount < items.length) {
    let renderedCount = visibleCount;
    const sentinel = node("div", "gallery-sentinel");
    const status = node("span", "gallery-sentinel-status");
    sentinel.dataset.gallerySentinel = "";
    sentinel.setAttribute("role", "status");
    sentinel.setAttribute("aria-live", "polite");
    sentinel.setAttribute("aria-atomic", "true");
    sentinel.setAttribute("aria-busy", "false");

    const updateSentinel = () => {
      const remaining = items.length - renderedCount;
      const nextBatchSize = Math.min(PAGE_SIZE, remaining);
      sentinel.dataset.nextBatchSize = String(nextBatchSize);
      status.textContent = `${formatNumber(remaining)} more resource${remaining === 1 ? "" : "s"} available`;
    };
    updateSentinel();
    sentinel.append(status);
    section.append(sentinel);

    galleryLoaders.set(sentinel, () => {
      const nextCount = Math.min(renderedCount + PAGE_SIZE, items.length);
      const fragment = document.createDocumentFragment();
      for (const resource of items.slice(renderedCount, nextCount)) {
        fragment.append(resourceCard(resource, options));
      }
      grid.append(fragment);
      renderedCount = nextCount;
      state.visibleLimit = Math.max(state.visibleLimit, renderedCount);
      count.textContent = `${formatNumber(renderedCount)} of ${formatNumber(items.length)}`;
      sentinel.classList.remove("is-loading");
      sentinel.setAttribute("aria-busy", "false");
      if (renderedCount >= items.length) {
        sentinel.classList.add("is-complete");
        sentinel.removeAttribute("data-next-batch-size");
        status.textContent = `All ${formatNumber(items.length)} resources loaded`;
        return false;
      }
      updateSentinel();
      return true;
    });
  }
  return section;
}

function resourceCard(resource, options = {}) {
  const presentation = resource.presentation || {};
  const article = node("article", "resource-card");
  article.dataset.resourceId = resource.resourceId;

  article.append(interactivePreviewSurface(
    resource,
    "card",
    event => openDetails(resource.resourceId, event.currentTarget)
  ));

  const body = node("div", "card-body");
  body.append(node("p", "card-source", `${presentation.source || resource.host || "Unknown source"} / ${presentation.format || resource.kind || "Resource"}`));
  const title = node("h3", "resource-title");
  const titleButton = node("button", "card-title-button", resourceDisplayTitle(resource));
  titleButton.type = "button";
  titleButton.addEventListener("click", event => openDetails(resource.resourceId, event.currentTarget));
  title.append(titleButton);
  body.append(title, node("p", "card-brief", resource.brief || "No concise description yet."));

  const cueText = presentation.decisionCue || presentation.contextCue || "Review whether this resource still supports current work.";
  const cue = node("p", "card-cue");
  cue.append(node("span", "", "Next"), document.createTextNode(` ${cueText}`));
  body.append(cue);

  const chips = resourceChips(resource, options.hideSpace);
  if (chips.childElementCount) body.append(chips);
  article.append(body, resourceCommands(resource, "card"), resourceFooter(resource, "card"));
  return article;
}

function previewVisual(resource, size, allowRemote) {
  const presentation = resource.presentation || {};
  const preview = presentation.preview || {};
  const frame = node("div", `preview-frame ${size} accent-${preview.accent || "teal"}`);
  const fallback = sourceFallback(resource);
  const localImage = String(preview.localImage || "");
  if (localImage) {
    const image = document.createElement("img");
    image.className = "preview-image";
    image.alt = `Preview for ${resourceDisplayTitle(resource)}`;
    image.loading = size === "detail" ? "eager" : "lazy";
    image.decoding = "async";
    image.addEventListener("error", () => frame.replaceChildren(fallback));
    image.src = localImage;
    frame.append(
      image,
      node("span", "preview-evidence-label", preview.evidenceLabel || "Saved image")
    );
    return frame;
  }

  frame.append(fallback);
  const remoteImage = String(preview.remoteImage || "");
  if (allowRemote && /^https:\/\//i.test(remoteImage)) {
    const load = node("button", "remote-load", "Load remote preview");
    load.type = "button";
    load.title = "Loads this preview from its remote source only after this click.";
    load.addEventListener("click", () => {
      load.disabled = true;
      load.textContent = "Loading preview";
      const image = document.createElement("img");
      image.className = "preview-image";
      image.alt = `Preview for ${resourceDisplayTitle(resource)}`;
      image.referrerPolicy = "no-referrer";
      image.addEventListener("load", () => frame.replaceChildren(image));
      image.addEventListener("error", () => {
        load.disabled = false;
        load.textContent = "Preview unavailable";
        frame.replaceChildren(fallback, load);
      });
      image.src = remoteImage;
    });
    frame.append(load);
  }
  return frame;
}

function interactivePreviewSurface(resource, size, openDetails = null) {
  const surface = node("div", `preview-surface ${size}`);
  const frame = previewVisual(resource, size, size === "detail");
  const motion = normalizedMotionPreview(resource);
  surface.append(frame);

  let detailsButton = null;
  if (openDetails) {
    detailsButton = node("button", "preview-open-details");
    detailsButton.type = "button";
    detailsButton.setAttribute("aria-label", `Open details for ${resourceDisplayTitle(resource)}`);
    detailsButton.addEventListener("click", openDetails);
    surface.append(detailsButton);
  }

  if (!motion) return surface;
  const trigger = node("button", "motion-preview-trigger");
  trigger.type = "button";
  trigger.title = motion.label;
  trigger.setAttribute("aria-label", motion.label);
  trigger.append(node("span", "motion-play-icon"));
  surface.append(trigger);

  trigger.addEventListener("click", event => {
    event.preventDefault();
    event.stopPropagation();
    if (activeMotionPreview?.surface === surface) stopActiveMotionPreview();
    else startMotionPreview(surface, resource, motion, size, detailsButton, trigger);
  });
  surface.addEventListener("pointerenter", event => {
    if (event.pointerType === "touch" || !automaticMotionAllowed()) return;
    scheduleMotionLoad(surface, resource, motion, size, detailsButton, trigger);
  });
  surface.addEventListener("pointerleave", () => {
    cancelMotionLoad(surface);
    if (activeMotionPreview?.surface === surface) stopActiveMotionPreview();
  });
  return surface;
}

function normalizedMotionPreview(resource) {
  const motion = resource.presentation?.preview?.motion || {};
  if (motion.kind === "youtube" && /^[A-Za-z0-9_-]{6,32}$/.test(String(motion.videoId || ""))) {
    if (!/^https?:$/.test(window.location.protocol)) return null;
    return {
      kind: "youtube",
      videoId: String(motion.videoId),
      origin: "https://www.youtube-nocookie.com",
      label: String(motion.label || "Play video preview")
    };
  }
  if (motion.kind !== "x_mp4") return null;
  try {
    const url = new URL(String(motion.url || ""));
    const allowedPath = /^\/(?:amplify_video|ext_tw_video|tweet_video)\//i.test(url.pathname);
    if (url.protocol !== "https:" || url.hostname !== "video.twimg.com" || url.port || !allowedPath || !/\.mp4$/i.test(url.pathname)) {
      return null;
    }
    return {
      kind: "x_mp4",
      url: url.href,
      origin: url.origin,
      label: String(motion.label || "Play post video preview")
    };
  } catch (_error) {
    return null;
  }
}

function automaticMotionAllowed() {
  const reducedMotion = window.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches;
  return !reducedMotion && !navigator.connection?.saveData;
}

function scheduleMotionLoad(surface, resource, motion, size, detailsButton, trigger) {
  cancelMotionLoad(surface);
  surface._motionPreconnectTimer = motionTimer(140, () => ensureConnectionHint(motion.origin));
  surface._motionStartTimer = motionTimer(480, () => {
    surface._motionStartTimer = null;
    if (surface.isConnected) {
      startMotionPreview(surface, resource, motion, size, detailsButton, trigger);
    }
  });
}

function motionTimer(delay, callback) {
  const timer = window.setTimeout(() => {
    motionLoadTimers.delete(timer);
    callback();
  }, delay);
  motionLoadTimers.add(timer);
  return timer;
}

function cancelMotionLoad(surface) {
  for (const key of ["_motionPreconnectTimer", "_motionStartTimer"]) {
    const timer = surface[key];
    if (!timer) continue;
    window.clearTimeout(timer);
    motionLoadTimers.delete(timer);
    surface[key] = null;
  }
}

function ensureConnectionHint(origin) {
  if (!origin || connectionHints.has(origin)) return;
  const hint = document.createElement("link");
  hint.rel = "preconnect";
  hint.href = origin;
  hint.crossOrigin = "anonymous";
  document.head.append(hint);
  connectionHints.add(origin);
}

function startMotionPreview(surface, resource, motion, size, detailsButton, trigger) {
  if (!surface.isConnected || activeMotionPreview?.surface === surface) return;
  stopActiveMotionPreview();
  cancelMotionLoad(surface);
  ensureConnectionHint(motion.origin);

  const layer = node("div", `motion-preview-layer motion-${motion.kind}`);
  layer.setAttribute("aria-label", `Live preview for ${resourceDisplayTitle(resource)}`);
  surface.classList.add("is-motion-active");
  if (detailsButton) detailsButton.hidden = true;
  trigger.hidden = true;
  surface.append(layer);

  let media = null;
  let removeMediaListeners = () => {};
  const stop = () => {
    removeMediaListeners();
    if (media instanceof HTMLVideoElement) {
      media.pause();
      media.removeAttribute("src");
      media.load();
    } else if (media instanceof HTMLIFrameElement) {
      media.removeAttribute("srcdoc");
      media.src = "about:blank";
    }
    layer.remove();
    surface.classList.remove("is-motion-active", "is-motion-playing");
    if (detailsButton) detailsButton.hidden = false;
    trigger.hidden = false;
  };
  activeMotionPreview = { surface, stop };

  if (motion.kind === "x_mp4") {
    const iframe = document.createElement("iframe");
    media = iframe;
    iframe.className = "motion-preview-player";
    iframe.title = `Video preview for ${resourceDisplayTitle(resource)}`;
    iframe.allow = "autoplay; picture-in-picture";
    iframe.setAttribute("sandbox", "allow-scripts");
    const receiveMotionState = event => {
      if (event.source !== iframe.contentWindow || event.data?.source !== "tab-atlas-motion-preview") return;
      if (event.data.state === "playing") surface.classList.add("is-motion-playing");
      if (event.data.state === "error") failMotionPreview(surface);
    };
    window.addEventListener("message", receiveMotionState);
    removeMediaListeners = () => window.removeEventListener("message", receiveMotionState);
    iframe.srcdoc = xMotionDocument(motion.url, size === "detail");
    layer.append(iframe);
    return;
  }

  const iframe = document.createElement("iframe");
  media = iframe;
  iframe.className = "motion-preview-player";
  iframe.title = `Video preview for ${resourceDisplayTitle(resource)}`;
  iframe.allow = "autoplay; encrypted-media; picture-in-picture";
  iframe.referrerPolicy = "strict-origin-when-cross-origin";
  iframe.addEventListener("load", () => surface.classList.add("is-motion-playing"));
  iframe.addEventListener("error", () => failMotionPreview(surface));
  const controls = size === "detail" ? "1" : "0";
  iframe.src = `${motion.origin}/embed/${motion.videoId}?autoplay=1&mute=1&controls=${controls}&playsinline=1&rel=0`;
  layer.append(iframe);
}

function xMotionDocument(url, controls) {
  const safeUrl = String(url)
    .replaceAll("&", "&amp;")
    .replaceAll('"', "&quot;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;");
  const controlsAttribute = controls ? " controls" : "";
  return `<!doctype html>
<html><head>
<meta name="referrer" content="no-referrer">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; media-src https://video.twimg.com; style-src 'unsafe-inline'; script-src 'unsafe-inline'">
<style>html,body,video{width:100%;height:100%;margin:0;background:#000}video{display:block;object-fit:cover}</style>
</head><body>
<video autoplay muted loop playsinline preload="metadata"${controlsAttribute} src="${safeUrl}"></video>
<script>
const video=document.querySelector("video");
const notify=state=>parent.postMessage({source:"tab-atlas-motion-preview",state},"*");
video.addEventListener("playing",()=>notify("playing"),{once:true});
video.addEventListener("error",()=>notify("error"),{once:true});
video.play().catch(()=>{video.controls=true});
</script>
</body></html>`;
}

function failMotionPreview(surface) {
  if (activeMotionPreview?.surface !== surface) return;
  stopActiveMotionPreview();
  elements.actionStatus.textContent = "Live preview unavailable; the saved preview remains available.";
}

function stopActiveMotionPreview() {
  if (!activeMotionPreview) return;
  const current = activeMotionPreview;
  activeMotionPreview = null;
  current.stop();
}

function sourceFallback(resource) {
  const presentation = resource.presentation || {};
  const fallback = node("div", "source-fallback");
  fallback.setAttribute("aria-hidden", "true");
  const identity = presentation.publisher || presentation.source || resource.host || "Resource";
  const summary = resource.brief || presentation.contextCue || resource.displayUrl || "Stored page metadata";
  fallback.append(
    node("span", "fallback-source", identity),
    node("strong", "fallback-title", resourceDisplayTitle(resource)),
    node("span", "fallback-summary", summary),
    node("span", "fallback-meta", `${presentation.format || resource.kind || "Resource"} / ${presentation.intent || "Reference"}`),
    node(
      "span",
      "preview-evidence-label",
      presentation.preview?.metadataLabel || "Metadata preview"
    )
  );
  return fallback;
}

function resourceChips(resource, hideSpace = false) {
  const chips = node("div", "resource-chips");
  const space = resourceSpace(resource);
  const topics = resourceTopics(resource);
  const focuses = resourceFocuses(resource);
  const projects = resourceProjects(resource);
  const actionLists = resourceActionLists(resource);
  const duplicate = duplicateSummary(resource);
  if (!hideSpace && space) chips.append(chip(space, "space-chip"));
  if (topics[0]) chips.append(chip(topics[0], "topic-chip"));
  if (focuses[0]) chips.append(chip(focuses[0], "focus-chip"));
  else if (projects[0]) chips.append(chip(projects[0], "project-chip"));
  if (actionLists[0]) chips.append(chip(actionLists[0], "action-chip"));
  if (Number(resource.noteCount || 0)) chips.append(chip("Your note", "note-chip"));
  if (duplicate.sets) {
    const count = duplicate.safeCloseCandidates || duplicate.instances;
    const qualifier = duplicate.safeCloseCandidates ? "" : " protected";
    chips.append(chip(`${count}${qualifier} exact ${count === 1 ? "copy" : "copies"}`, "duplicate-chip"));
  }
  return chips;
}

function resourceCommands(resource, placement) {
  const wrapper = node("div", `resource-commands ${placement}-resource-commands`);
  const sourceUrl = resourceSourceUrl(resource);
  if (sourceUrl) {
    const open = node("a", "resource-command-link", "Open source");
    open.href = sourceUrl;
    open.target = "_blank";
    open.rel = "noreferrer";
    wrapper.append(open);

    const copy = node("button", "resource-command-button", "Copy link");
    copy.type = "button";
    copy.addEventListener("click", () => copyResourceLink(resource));
    wrapper.append(copy);
  }

  const menu = document.createElement("details");
  menu.className = "resource-command-menu";
  const summary = node("summary", "", "More");
  summary.title = `More actions for ${resourceDisplayTitle(resource)}`;
  menu.append(summary);
  const choices = node("div", "resource-command-choices");
  const preview = resource.presentation?.preview || {};
  if (!preview.localImage && preview.canRequestRicher !== false) {
    const requestLabel = preview.requestLabel || "richer preview";
    choices.append(menuCommand(`Request ${requestLabel}`, () => requestResourcePreview(resource)));
  }
  choices.append(menuCommand("Reclassify", () => requestResourceReclassification(resource)));
  if (resource.libraryState === "accepted") {
    choices.append(menuCommand("Remove from library", () => requestLibraryRemoval(resource), "danger"));
  }
  menu.append(choices);
  wrapper.append(menu);
  return wrapper;
}

function menuCommand(label, onClick, tone = "") {
  const button = node("button", tone, label);
  button.type = "button";
  button.addEventListener("click", event => {
    onClick();
    const menu = event.currentTarget.closest("details");
    if (menu) menu.open = false;
  });
  return button;
}

function resourceFooter(resource, placement) {
  const openCount = liveTabs(resource).length;
  const duplicateCount = duplicateSummary(resource).safeCloseCandidates;
  const wrapper = node("div", `resource-footer ${placement}-resource-footer`);
  if (resource.libraryState === "candidate") {
    wrapper.classList.add("has-command", "is-discovery");
    const copy = node("span", "resource-footer-copy");
    copy.append(
      node("strong", "", "New discovery"),
      node("span", "", "Not yet in the durable library")
    );
    const button = node("button", "resource-action-command accept-resource-command", "Add to library");
    button.type = "button";
    button.addEventListener("click", () => requestDiscoveryAcceptance(resource));
    wrapper.append(copy, button);
    return wrapper;
  }
  if (duplicateCount) {
    wrapper.classList.add("has-command");
    const copy = node("span", "resource-footer-copy");
    copy.append(
      node("strong", "", `${formatNumber(duplicateCount)} safe ${duplicateCount === 1 ? "extra" : "extras"}`),
      node("span", "", "Request only; verified first")
    );
    const button = node("button", "resource-action-command", "Close duplicate extras");
    button.type = "button";
    button.title = "Downloads a privacy-safe action request; it does not close tabs directly.";
    button.setAttribute("aria-label", `Close duplicate extras for ${resourceDisplayTitle(resource)}. ${REQUEST_SAFETY_TEXT}`);
    button.addEventListener("click", () => requestDuplicateClose(resource));
    wrapper.append(copy, button);
    return wrapper;
  }

  wrapper.classList.add(openCount ? "is-open" : "is-stored");
  const copy = node("span", "resource-footer-copy");
  copy.append(
    node("strong", "", openCount ? `${formatNumber(openCount)} open ${openCount === 1 ? "tab" : "tabs"}` : "Stored"),
    node("span", "", openCount ? "Captured in library" : "No open browser tabs")
  );
  wrapper.append(copy);
  return wrapper;
}

function renderDrawer() {
  const resource = state.selectedResource ? resourceById.get(state.selectedResource) : null;
  if (!resource) {
    elements.drawerHost.replaceChildren();
    elements.topbar.inert = false;
    elements.primaryNav.inert = false;
    elements.screen.inert = false;
    document.body.classList.remove("drawer-open");
    return;
  }

  elements.topbar.inert = true;
  elements.primaryNav.inert = true;
  elements.screen.inert = true;
  document.body.classList.add("drawer-open");
  const backdrop = node("div", "drawer-backdrop");
  backdrop.addEventListener("click", event => {
    if (event.target === backdrop) closeDetails();
  });
  const drawer = node("aside", "detail-drawer");
  drawer.setAttribute("role", "dialog");
  drawer.setAttribute("aria-modal", "true");
  drawer.setAttribute("aria-label", `Details for ${resourceDisplayTitle(resource)}`);

  const top = node("header", "drawer-top");
  top.append(node("span", "drawer-kicker", `${resource.presentation?.source || resource.host || "Source"} / ${resource.presentation?.format || resource.kind || "Resource"}`));
  const close = node("button", "drawer-close");
  close.type = "button";
  close.innerHTML = "&times;";
  close.title = "Close details";
  close.setAttribute("aria-label", "Close details");
  close.addEventListener("click", closeDetails);
  top.append(close);
  drawer.append(top, interactivePreviewSurface(resource, "detail"));

  const content = node("div", "drawer-content");
  const titleBlock = node("section", "detail-title");
  titleBlock.append(node("h2", "", resourceDisplayTitle(resource)));
  titleBlock.append(resourceCommands(resource, "drawer"));
  content.append(titleBlock);

  content.append(resourceNoteSection(resource));
  if (workspace.interactive && !workspace.noteCache.has(resource.resourceId)) {
    loadResourceWorkspaceState(resource.resourceId);
  }
  const actionProgress = resourceActionProgressSection(resource);
  if (actionProgress) content.append(actionProgress);

  const glance = node("section", "detail-section");
  glance.append(node("h3", "", "At a glance"), node("p", "detail-brief", resource.brief || "No concise description yet."));
  const cues = node("dl", "cue-list");
  cues.append(
    cue("Relevance", resource.presentation?.contextCue),
    cue("Next", resource.presentation?.decisionCue)
  );
  glance.append(cues);
  content.append(glance);

  const categories = detailCategories(resource);
  if (categories) content.append(categories);
  const duplicate = duplicateSummary(resource);
  if (duplicate.sets) content.append(duplicateDetails(resource, duplicate));
  content.append(metadataSection(resource));

  if (resource.whyKept || resource.detail) {
    const more = document.createElement("details");
    more.className = "detail-disclosure";
    more.append(node("summary", "", "More context"));
    if (resource.whyKept) more.append(detailText("Why it was kept", resource.whyKept));
    if (resource.detail) more.append(detailText("Details", resource.detail));
    content.append(more);
  }
  const contexts = instanceDisclosure(resource);
  if (contexts) content.append(contexts);
  drawer.append(content, resourceFooter(resource, "drawer"));
  backdrop.append(drawer);
  elements.drawerHost.replaceChildren(backdrop);
  requestAnimationFrame(() => close.focus({ preventScroll: true }));
}

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

function detailCategories(resource) {
  const values = [
    ["Space", resourceSpace(resource)],
    ["Topics", resourceTopics(resource).join(", ")],
    ["Focus", resourceFocuses(resource).join(", ")],
    ["Projects", resourceProjects(resource).join(", ")],
    ["Action lists", resourceActionLists(resource).join(", ")],
    ["Browser groups", contextGroupTitles(resource).join(", ")]
  ].filter(item => item[1]);
  if (!values.length) return null;
  const section = node("section", "detail-section");
  section.append(node("h3", "", "Context"));
  const list = node("dl", "detail-facts");
  for (const [label, value] of values) list.append(cue(label, value));
  section.append(list);
  return section;
}

function duplicateDetails(resource, duplicate) {
  const section = node("section", "detail-section duplicate-detail");
  section.append(
    node("h3", "", "Exact duplicates"),
    node("p", "detail-note", `${duplicate.safeCloseCandidates} safe close candidate${duplicate.safeCloseCandidates === 1 ? "" : "s"}; ${duplicate.protectedInstances} protected instance${duplicate.protectedInstances === 1 ? "" : "s"}.`)
  );
  const list = node("div", "duplicate-list");
  for (const group of exactDuplicateGroups(resource)) {
    const row = node("div", "duplicate-row");
    const label = `${capitalize(group.browser)}${group.groupTitle ? ` / ${group.groupTitle}` : ""}`;
    row.append(
      node("strong", "", label),
      node("span", "", `${group.tabs.length} exact tabs / ${group.safe} safe to close`)
    );
    list.append(row);
  }
  section.append(list);
  return section;
}

function metadataSection(resource) {
  const contexts = resourceContexts(resource);
  const openCount = liveTabs(resource).length;
  const section = node("section", "detail-section");
  section.append(node("h3", "", "Metadata"));
  const facts = node("dl", "metadata-grid");
  facts.append(
    cue("Source", resource.presentation?.source || resource.host),
    cue("Owner/site", resource.presentation?.publisher),
    cue("Format", resource.presentation?.format || resource.kind),
    cue("Intent", resource.presentation?.intent),
    cue("Open tabs", String(openCount)),
    cue("Captured in", unique(contexts.map(context => capitalize(context.browser)).filter(Boolean)).join(", ")),
    cue("Library state", openCount ? "Open in browser" : "Stored"),
    cue("First seen", formatDate(resource.firstSeenAt, true))
  );
  section.append(facts);
  return section;
}

function detailText(label, value) {
  const wrapper = node("div", "detail-text");
  wrapper.append(node("strong", "", label), node("p", "", value));
  return wrapper;
}

function instanceDisclosure(resource) {
  const contexts = resourceContexts(resource);
  if (!contexts.length) return null;
  const details = document.createElement("details");
  details.className = "detail-disclosure";
  details.append(node("summary", "", `Captured contexts (${contexts.length})`));
  const list = node("div", "instance-list");
  for (const context of contexts) {
    const item = node("div", "instance-row");
    const contextDetails = [];
    if (context.position !== null && context.position !== undefined && Number.isFinite(Number(context.position))) {
      contextDetails.push(`Position ${Number(context.position) + 1}`);
    }
    if (context.live === true) contextDetails.push("current capture");
    else if (context.live === false) contextDetails.push("stored capture");
    if (context.pinned) contextDetails.push("pinned");
    if (context.active) contextDetails.push("active");
    if (context.audible) contextDetails.push("audible");
    item.append(
      node("strong", "", `${capitalize(context.browser)}${context.groupTitle ? ` / ${context.groupTitle}` : ""}`),
      node("span", "", contextDetails.join(" / ") || "Captured browser context")
    );
    list.append(item);
  }
  details.append(list);
  return details;
}

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
  return allResources.filter(resource => duplicateSummary(resource).sets > 0).sort((a, b) => {
    const count = duplicateSummary(b).safeCloseCandidates - duplicateSummary(a).safeCloseCandidates;
    return count || resourceSort(a, b);
  });
}

function openTabResources() {
  return allResources.filter(resource => liveTabs(resource).length > 0).sort(resourceSort);
}

function safeDuplicateCount() {
  return allResources.reduce((total, resource) => total + duplicateSummary(resource).safeCloseCandidates, 0);
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
  if (mode === "discoveries") return "New canonical resources from the latest capture. Accept selected items or the complete batch before archiving their tabs.";
  if (mode === "duplicates") return "Exact URL matches within the same browser window and group. Protected tabs remain visible and excluded from safe candidates.";
  if (mode === "open") return "Resources represented by the latest captured browser state. Their metadata and reopen links remain in the durable library after tabs close.";
  return "Stored or open resources without a purpose space. Organize them without keeping browser tabs alive.";
}

function reviewEmptyMessage(mode) {
  if (mode === "discoveries") return "No new resources are waiting for approval.";
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

function requestCapturedTabArchive() {
  const currentTabs = currentTabCount();
  if (!currentTabs || pendingDiscoveryCount() || inventoryCount("currentDismissedResources", 0)) return;
  downloadActionRequest("archive_captured_tabs", {
    libraryResources: libraryResourceCount(),
    currentResources: currentResourceCount(),
    currentTabs
  });
}

function requestDiscoveryAcceptance(resource = null) {
  const selected = resource
    ? [resource.resourceId]
    : discoveries.map(item => item.resourceId);
  const count = selected.length;
  if (!count) return;
  downloadActionRequest("accept_discoveries", {
    pendingDiscoveries: pendingDiscoveryCount(),
    selectedResources: count
  }, selected);
}

function requestDuplicateClose(resource = null) {
  const safeDuplicateCandidates = resource
    ? duplicateSummary(resource).safeCloseCandidates
    : safeDuplicateCount();
  if (!safeDuplicateCandidates) return;
  const matchingResources = resource
    ? 1
    : exactDuplicateResources().filter(item => duplicateSummary(item).safeCloseCandidates > 0).length;
  downloadActionRequest("close_exact_duplicates", {
    libraryResources: libraryResourceCount(),
    currentResources: currentResourceCount(),
    currentTabs: currentTabCount(),
    matchingResources,
    safeDuplicateCandidates
  }, resource ? [resource.resourceId] : []);
}

async function requestLibraryRemoval(resource) {
  if (!resource || resource.libraryState !== "accepted") return;
  if (workspace.interactive) {
    if (!window.confirm("Remove this resource from the visible library? It remains recoverable and no browser tab will be changed.")) return;
    try {
      await workspaceRequest(`/api/v1/resources/${resource.resourceId}/remove`, { method: "POST" });
      elements.actionStatus.textContent = "Resource removed from the visible library.";
      window.location.reload();
    } catch (error) {
      showWorkspaceError(error, "The resource could not be removed.");
    }
    return;
  }
  downloadActionRequest("remove_from_library", {
    libraryResources: libraryResourceCount(),
    selectedResources: 1
  }, [resource.resourceId]);
}

function requestResourcePreview(resource) {
  if (!resource) return;
  downloadActionRequest("capture_resource_preview", {
    selectedResources: 1,
    existingImagePreviews: resource.presentation?.preview?.localImage ? 1 : 0
  }, [resource.resourceId]);
}

function requestResourceReclassification(resource) {
  if (!resource) return;
  if (workspace.interactive) {
    state.selectedResource = resource.resourceId;
    renderDrawer();
    setAgentPanel(true);
    elements.agentInput.placeholder = "Describe what this resource means to you";
    elements.actionStatus.textContent = "Codex is scoped to this resource.";
    return;
  }
  downloadActionRequest("reconsider_resource", {
    selectedResources: 1,
    libraryResources: libraryResourceCount()
  }, [resource.resourceId]);
}

function resourceSourceUrl(resource) {
  const value = String(resource?.openUrl || resource?.canonicalUrl || "");
  return /^(https?|file):/i.test(value) ? value : "";
}

async function copyResourceLink(resource) {
  const value = resourceSourceUrl(resource);
  if (!value) return;
  let copied = false;
  try {
    await navigator.clipboard.writeText(value);
    copied = true;
  } catch {
    const input = document.createElement("textarea");
    input.value = value;
    input.setAttribute("readonly", "");
    input.style.position = "fixed";
    input.style.opacity = "0";
    document.body.append(input);
    input.select();
    copied = document.execCommand("copy");
    input.remove();
  }
  elements.actionStatus.textContent = copied ? "Source link copied." : "The source link could not be copied.";
}

function downloadActionRequest(action, counts, resourceIds = []) {
  const payload = {
    schemaVersion: 1,
    action,
    generatedAt: new Date().toISOString()
  };
  const safeResourceIds = unique(resourceIds.filter(resourceId => resourceById.has(resourceId)));
  if (safeResourceIds.length) payload.resourceIds = safeResourceIds;
  payload.counts = Object.fromEntries(Object.entries(counts).map(([key, value]) => [key, Math.max(0, Number(value) || 0)]));
  const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = "tabatlas-action-request.json";
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
  elements.actionStatus.textContent = "Action request downloaded. No browser tabs were changed.";
}

async function initializeWorkspace() {
  if (!/^https?:$/.test(window.location.protocol)) return;
  try {
    const response = await fetch("/api/v1/session", { credentials: "same-origin", cache: "no-store" });
    if (!response.ok) return;
    const session = await response.json();
    workspace.interactive = Boolean(session.interactive);
    workspace.csrfToken = String(session.csrfToken || "");
    mergeWorkspaceSession(session);
    elements.agentToggle.hidden = !workspace.interactive;
    render();
  } catch (_error) {
    workspace.interactive = false;
    elements.agentToggle.hidden = true;
  }
}

function mergeWorkspaceSession(session) {
  workspace.agent = session.agent || {};
  spaceSummaries = Array.isArray(session.spaceSummaries) ? session.spaceSummaries : spaceSummaries;
  projectSummaries = Array.isArray(session.projectSummaries) ? session.projectSummaries : projectSummaries;
  actionListSummaries = Array.isArray(session.actionLists) ? session.actionLists : actionListSummaries;
  for (const [resourceId, count] of Object.entries(session.resourceNoteCounts || {})) {
    const resource = resourceById.get(resourceId);
    if (resource) resource.noteCount = Number(count || 0);
  }
  workspace.latestAuditByResource.clear();
  for (const audit of session.recentAudits || []) {
    if (!audit.undoneAt && !workspace.latestAuditByResource.has(audit.resourceId)) {
      workspace.latestAuditByResource.set(audit.resourceId, audit.id);
    }
  }
  for (const request of session.pendingRequests || []) {
    pollAgentRequest(request.id, request.resourceId || "");
  }
  for (const proposal of session.pendingProposals || []) {
    if (workspace.messages.some(message => message.proposalId === proposal.id)) continue;
    workspace.messages.push({
      role: "assistant",
      text: proposal.message,
      proposal: proposal.response || null,
      proposalId: proposal.id,
      decision: "",
      resourceId: proposal.resourceId
    });
  }
}

async function refreshWorkspaceSession() {
  if (!workspace.interactive) return;
  const session = await workspaceRequest("/api/v1/session");
  mergeWorkspaceSession(session);
}

async function workspaceRequest(path, options = {}) {
  const method = String(options.method || "GET").toUpperCase();
  const headers = new Headers(options.headers || {});
  let body = options.body;
  if (Object.prototype.hasOwnProperty.call(options, "json")) {
    body = JSON.stringify(options.json);
    headers.set("Content-Type", "application/json");
  } else if (options.contentType) {
    headers.set("Content-Type", options.contentType);
  }
  if (method !== "GET" && method !== "HEAD") {
    headers.set("X-TabAtlas-CSRF", workspace.csrfToken);
    headers.set("Idempotency-Key", options.idempotencyKey || workspaceIdempotencyKey());
  }
  const response = await fetch(path, {
    method,
    headers,
    body,
    credentials: "same-origin",
    cache: "no-store"
  });
  const contentType = response.headers.get("Content-Type") || "";
  const payload = contentType.includes("application/json") ? await response.json() : null;
  if (!response.ok) {
    const error = new Error(payload?.error || `Workspace request failed (${response.status})`);
    error.code = payload?.code || `http_${response.status}`;
    throw error;
  }
  return payload;
}

function workspaceIdempotencyKey() {
  if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID();
  return `workspace-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function setAgentPanel(open) {
  if (!workspace.interactive) return;
  state.agentOpen = Boolean(open);
  renderAgentPanel();
  if (state.agentOpen) requestAnimationFrame(() => elements.agentInput.focus({ preventScroll: true }));
}

function renderAgentPanel() {
  elements.agentPanel.hidden = !state.agentOpen || !workspace.interactive;
  elements.agentToggle.setAttribute("aria-expanded", String(state.agentOpen));
  if (!workspace.interactive) return;
  const pendingCount = workspace.messages.filter(message => message.proposalId && !message.decision).length;
  elements.agentPending.textContent = String(pendingCount);
  elements.agentPending.hidden = pendingCount === 0;
  const selected = state.selectedResource ? resourceById.get(state.selectedResource) : null;
  elements.agentScope.textContent = selected ? resourceDisplayTitle(selected) : "Whole library";
  elements.agentInput.placeholder = selected
    ? "Describe what this resource means to you"
    : "Ask about this resource library";
  const ownership = workspace.agent.ownership || "workspace";
  elements.agentStatus.textContent = ownership === "codex_desktop"
    ? "Open in Codex desktop"
    : (workspace.agent.running ? workspace.agent.model || "Connected" : "Starts when asked");
  elements.agentOwnership.textContent = ownership === "codex_desktop" ? "Reclaim here" : "Open in Codex";
  elements.agentMessages.replaceChildren();
  if (!workspace.messages.length) {
    elements.agentMessages.append(node("p", "agent-empty", selected ? "Ask about the selected resource." : "Ask across the retained library."));
  }
  for (const message of workspace.messages) {
    const item = node("div", `agent-message ${message.role}`);
    item.append(node("p", "", message.text));
    if (message.proposalId && message.proposal) item.append(agentProposalSummary(message.proposal));
    const actions = node("div", "agent-message-actions");
    if (message.proposalId && !message.decision) {
      const apply = node("button", "primary-command", "Accept changes");
      apply.type = "button";
      apply.addEventListener("click", () => decideAgentProposal(message, true, apply));
      const dismiss = node("button", "text-command", "Dismiss");
      dismiss.type = "button";
      dismiss.addEventListener("click", () => decideAgentProposal(message, false, dismiss));
      const discuss = node("button", "secondary-command", "Discuss");
      discuss.type = "button";
      discuss.addEventListener("click", () => discussAgentProposal(message));
      actions.append(apply, discuss, dismiss);
    } else if (message.decision) {
      const label = message.decision === "accepted"
        ? "Accepted"
        : (message.decision === "rejected" ? "Dismissed" : "Superseded");
      actions.append(node("span", "proposal-decision", label));
    }
    if (message.auditId) {
      const undo = node("button", "text-command", "Undo");
      undo.type = "button";
      undo.addEventListener("click", () => undoSemanticChange(message.resourceId, message.auditId, undo));
      actions.append(undo);
    }
    if (actions.childElementCount) item.append(actions);
    elements.agentMessages.append(item);
  }
  if (workspace.previousView) {
    const back = node("button", "agent-back", "Back before Codex navigation");
    back.type = "button";
    back.addEventListener("click", restoreAgentNavigation);
    elements.agentMessages.append(back);
  }
  elements.agentMessages.scrollTop = elements.agentMessages.scrollHeight;
}

function agentProposalSummary(proposal) {
  const summary = node("div", "agent-proposal");
  const memberships = Array.isArray(proposal.memberships) ? proposal.memberships : [];
  const primary = ["space", "topic", "focus"]
    .map(kind => memberships.find(item => item.kind === kind)?.name)
    .filter(Boolean);
  if (primary.length) summary.append(proposalLine("Purpose", primary.join(" / ")));
  const projects = memberships.filter(item => item.kind === "project").map(item => item.name);
  if (projects.length) summary.append(proposalLine("Projects", projects.join(", ")));
  const actionLists = memberships.filter(item => item.kind === "action_list").map(item => item.name);
  if (actionLists.length) summary.append(proposalLine("Action Lists", actionLists.join(", ")));
  if (proposal.actionItem?.listName) {
    const effort = proposal.actionItem.estimatedMinutes ? `, ${formatNumber(proposal.actionItem.estimatedMinutes)} min` : "";
    summary.append(proposalLine("Next", `${proposal.actionItem.listName}, priority ${proposal.actionItem.priority || 3}${effort}`));
  }
  if (!summary.childElementCount) summary.append(proposalLine("Suggestion", "No organization change"));
  return summary;
}

function proposalLine(label, value) {
  const line = node("div", "agent-proposal-line");
  line.append(node("strong", "", label), node("span", "", value));
  return line;
}

function discussAgentProposal(message) {
  if (message.resourceId && resourceById.has(message.resourceId)) state.selectedResource = message.resourceId;
  elements.agentInput.value = "Refine this pending suggestion. Explain the tradeoffs and propose a better organization if needed.";
  renderAgentPanel();
  requestAnimationFrame(() => {
    elements.agentInput.focus({ preventScroll: true });
    elements.agentInput.setSelectionRange(elements.agentInput.value.length, elements.agentInput.value.length);
  });
}

async function submitAgentRequest(event) {
  event.preventDefault();
  const message = elements.agentInput.value;
  if (!message.trim()) return;
  const send = elements.agentForm.querySelector('button[type="submit"]');
  send.disabled = true;
  const resourceId = state.selectedResource || "";
  workspace.messages.push({ role: "user", text: message.trim() });
  elements.agentInput.value = "";
  renderAgentPanel();
  try {
    const request = await workspaceRequest("/api/v1/agent-requests", {
      method: "POST",
      json: { message, resourceId }
    });
    workspace.messages.push({ role: "status", text: "Codex is considering the current scope." });
    renderAgentPanel();
    pollAgentRequest(request.id, resourceId);
  } catch (error) {
    showWorkspaceError(error, "Codex request could not be queued.");
  } finally {
    send.disabled = false;
  }
}

function pollAgentRequest(requestId, resourceId = "") {
  if (!requestId || workspace.requestPolls.has(requestId)) return;
  let deferredChecks = 0;
  const poll = async () => {
    try {
      const request = await workspaceRequest(`/api/v1/agent-requests/${requestId}`);
      if (request.status === "queued" && request.errorCode) {
        deferredChecks += 1;
        if (deferredChecks >= 6) {
          workspace.messages.push({ role: "status", text: "Saved for the next available Codex session." });
          workspace.requestPolls.delete(requestId);
          renderAgentPanel();
          return;
        }
      }
      if (request.status === "failed") {
        workspace.messages = workspace.messages.filter(message => message.role !== "status");
        workspace.messages.push({ role: "assistant", text: "The note remains saved, but Codex could not complete this review in the current session." });
        workspace.requestPolls.delete(requestId);
        renderAgentPanel();
        return;
      }
      if (request.status !== "completed") {
        const timer = window.setTimeout(poll, request.errorCode ? 2500 : 1200);
        workspace.requestPolls.set(requestId, timer);
        return;
      }
      workspace.requestPolls.delete(requestId);
      workspace.messages = workspace.messages.filter(message => message.role !== "status");
      const response = request.response || {};
      workspace.messages.push({
        role: "assistant",
        text: response.message || response.interpretation || "Codex completed the request.",
        proposal: response,
        proposalId: request.proposalId,
        decision: request.decision || (request.proposalId && !request.proposalCurrent ? "superseded" : ""),
        auditId: request.auditId,
        resourceId: request.resourceId || resourceId
      });
      if (request.auditId && request.resourceId) workspace.latestAuditByResource.set(request.resourceId, request.auditId);
      if (request.resourceId) await loadResourceWorkspaceState(request.resourceId, true);
      await refreshWorkspaceSession();
      applyAgentNavigation(response.navigation);
      render();
    } catch (error) {
      workspace.requestPolls.delete(requestId);
      showWorkspaceError(error, "Codex status could not be read.");
    }
  };
  const timer = window.setTimeout(poll, 500);
  workspace.requestPolls.set(requestId, timer);
}

async function decideAgentProposal(message, accept, button) {
  button.disabled = true;
  try {
    const action = accept ? "accept" : "reject";
    const result = await workspaceRequest(`/api/v1/proposals/${message.proposalId}/${action}`, { method: "POST" });
    for (const candidate of workspace.messages) {
      if (candidate.proposalId === message.proposalId) {
        candidate.decision = result.decision;
        candidate.auditId = result.auditId;
      } else if (accept && result.resourceId && candidate.resourceId === result.resourceId && !candidate.decision) {
        candidate.decision = "superseded";
      }
    }
    if (result.auditId && result.resourceId) workspace.latestAuditByResource.set(result.resourceId, result.auditId);
    if (result.resourceId) await loadResourceWorkspaceState(result.resourceId, true);
    await refreshWorkspaceSession();
    elements.actionStatus.textContent = accept ? "Codex suggestion accepted and saved." : "Codex suggestion dismissed. No organization changed.";
    render();
  } catch (error) {
    showWorkspaceError(error, "The proposal decision could not be saved.");
  } finally {
    button.disabled = false;
  }
}

function applyAgentNavigation(navigation) {
  if (!navigation || navigation.command === "none") return;
  workspace.previousView = {
    view: state.view,
    search: state.search,
    scope: state.scope ? { ...state.scope } : null,
    sourceScope: state.sourceScope,
    sourcePublisher: state.sourcePublisher,
    sourceTopic: state.sourceTopic,
    reviewMode: state.reviewMode,
    filters: { ...state.filters },
    selectedResource: state.selectedResource
  };
  if (navigation.command === "open_resource" && resourceById.has(navigation.resourceId)) {
    state.selectedResource = navigation.resourceId;
    render();
    highlightResource(navigation.resourceId);
  } else if (navigation.command === "show_collection" && navigation.collectionName) {
    const summary = [...spaceSummaries, ...projectSummaries, ...actionListSummaries]
      .find(item => item.name === navigation.collectionName);
    if (summary) {
      const kind = actionListSummaries.includes(summary) ? "action_list" : (projectSummaries.includes(summary) ? "project" : "space");
      openScope(kind, summary.id);
    }
  } else if (navigation.command === "set_filter" && navigation.filter) {
    state.search = navigation.filter.toLocaleLowerCase();
    elements.search.value = navigation.filter;
    renderAtTop();
  }
  elements.actionStatus.textContent = "Codex changed the current view.";
}

function restoreAgentNavigation() {
  const previous = workspace.previousView;
  if (!previous) return;
  workspace.previousView = null;
  Object.assign(state, previous);
  elements.search.value = previous.search;
  renderAtTop();
}

function highlightResource(resourceId) {
  requestAnimationFrame(() => {
    const card = document.querySelector(`[data-resource-id="${CSS.escape(resourceId)}"]`);
    if (!card) return;
    card.classList.add("agent-highlight");
    card.scrollIntoView({ block: "center", behavior: "smooth" });
    window.setTimeout(() => card.classList.remove("agent-highlight"), 2400);
  });
}

async function toggleAgentOwnership() {
  elements.agentOwnership.disabled = true;
  try {
    if (workspace.agent.ownership === "codex_desktop") {
      const status = await workspaceRequest("/api/v1/agent/reclaim", { method: "POST" });
      workspace.agent = status;
      workspace.agent.ownership = "workspace";
    } else {
      const handoff = await workspaceRequest("/api/v1/agent/handoff", { method: "POST" });
      workspace.agent.ownership = handoff.ownership;
      renderAgentPanel();
      if (handoff.deepLink) window.location.href = handoff.deepLink;
    }
    renderAgentPanel();
  } catch (error) {
    showWorkspaceError(error, "Codex ownership could not be changed.");
  } finally {
    elements.agentOwnership.disabled = false;
  }
}

function showWorkspaceError(error, fallback) {
  const message = error?.message || fallback;
  elements.actionStatus.textContent = message;
  workspace.messages.push({ role: "assistant", text: message });
  renderAgentPanel();
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
  const fallback = allResources.reduce((total, resource) => total + liveTabs(resource).length, 0);
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
