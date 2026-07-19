const data = window.__TAB_ATLAS__ || {
  inventory: {},
  resources: [],
  groups: [],
  collectionSummaries: [],
  spaceSummaries: [],
  topicSummaries: [],
  projectSummaries: [],
  facets: {}
};

const STORAGE_KEY = "tabatlas.decisions.v1";
const PAGE_SIZE = 30;
const DECISIONS = [
  ["open", "Keep open"],
  ["saved", "Save + close"],
  ["close_candidate", "Dismiss + close"]
];
const hasSpaceContract = Object.prototype.hasOwnProperty.call(data, "spaceSummaries");
const resources = Array.isArray(data.resources) ? data.resources : [];
const resourceById = new Map(resources.map(resource => [resource.resourceId, resource]));
const validResourceIds = new Set(resourceById.keys());
const spaceSummaries = hasSpaceContract
  ? (data.spaceSummaries || [])
  : (data.collectionSummaries || []);
const projectSummaries = data.projectSummaries || [];

const state = {
  view: "home",
  search: "",
  scope: null,
  topic: "all",
  reviewMode: "inbox",
  filters: defaultFilters(),
  visibleLimit: PAGE_SIZE,
  selectedResource: null,
  decisions: loadDecisions(),
  lastFocus: null
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
    <button type="button" role="tab" data-view="spaces">Spaces</button>
    <button type="button" role="tab" data-view="review">Review <span id="navDecisionCount" class="nav-count" hidden>0</span></button>
  </nav>
  <main id="screen" class="screen"></main>
  <div id="drawerHost"></div>`;

const elements = {
  topbar: document.querySelector(".topbar"),
  homeBrand: document.getElementById("homeBrand"),
  freshness: document.getElementById("freshness"),
  search: document.getElementById("search"),
  primaryNav: document.getElementById("primaryNav"),
  navDecisionCount: document.getElementById("navDecisionCount"),
  screen: document.getElementById("screen"),
  drawerHost: document.getElementById("drawerHost")
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
document.addEventListener("keydown", event => {
  if (event.key !== "Escape") return;
  if (state.selectedResource) closeDetails();
  else if (state.search) clearSearch();
});

renderFreshness();
render();

function render() {
  updateNavigation();
  if (state.search) renderSearch();
  else if (state.view === "spaces") renderSpaces();
  else if (state.view === "review") renderReview();
  else renderHome();
  renderDrawer();
}

function renderFreshness() {
  const captures = data.inventory?.captures || [];
  elements.freshness.textContent = captures.length
    ? captures.map(item => `${capitalize(item.browser)} ${formatDate(item.capturedAt)}`).join(" / ")
    : "No capture imported yet";
}

function updateNavigation() {
  for (const button of elements.primaryNav.querySelectorAll("button[data-view]")) {
    const selected = !state.search && button.dataset.view === state.view;
    button.classList.toggle("active", selected);
    button.setAttribute("aria-selected", String(selected));
  }
  const queued = Object.keys(state.decisions).length;
  elements.navDecisionCount.textContent = String(queued);
  elements.navDecisionCount.hidden = queued === 0;
}

function renderHome() {
  const fragment = document.createDocumentFragment();
  const inbox = inboxResources();
  const safeDuplicates = safeDuplicateCount();
  const queued = queuedResources();
  const purposeSpaces = spaceSummaries.slice(0, 6);

  const intro = node("section", "home-head");
  const introCopy = node("div", "home-copy");
  introCopy.append(
    node("p", "eyebrow", "Current library"),
    node("h2", "", "Choose a space or clear the next decision queue"),
    node("p", "scope-summary", `${formatNumber(data.inventory?.currentResources || resources.length)} resources from ${formatNumber(data.inventory?.currentTabs || totalTabCount())} open tabs.`)
  );
  intro.append(introCopy, continueButton(inbox.length, safeDuplicates, queued.length));
  fragment.append(intro);

  const attention = node("section", "attention-section");
  attention.append(sectionHeading("Needs attention", "Small queues that can reduce uncertainty or open copies."));
  const attentionRow = node("div", "attention-row");
  attentionRow.append(
    attentionButton("Inbox", inbox.length, "No purpose space", () => openReview("inbox")),
    attentionButton("Safe exact duplicates", safeDuplicates, "Closeable copies", () => openReview("duplicates")),
    attentionButton("Queued decisions", queued.length, "Ready to export", () => openReview("queued"))
  );
  attention.append(attentionRow);
  fragment.append(attention);

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

function continueButton(inboxCount, duplicateCount, queuedCount) {
  const button = node("button", "continue-action");
  button.type = "button";
  let count = queuedCount;
  let label = "Review queued decisions";
  let detail = "Confirm the actions already marked in this report.";
  let onClick = () => openReview("queued");
  if (!queuedCount && inboxCount) {
    count = inboxCount;
    label = "Work through Inbox";
    detail = "Give unplaced resources a purpose or a closing decision.";
    onClick = () => openReview("inbox");
  } else if (!queuedCount && !inboxCount && duplicateCount) {
    count = duplicateCount;
    label = "Review exact duplicates";
    detail = "Inspect copies that can be closed without losing a unique URL.";
    onClick = () => openReview("duplicates");
  } else if (!queuedCount && !inboxCount && !duplicateCount) {
    count = data.inventory?.currentResources || resources.length;
    label = "Open purpose spaces";
    detail = "Browse the library by the work each resource supports.";
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

function renderScopeDetail(summary) {
  const members = resourcesForSummary(summary);
  const topics = scopeTopics(summary, members);
  const returnsHome = state.scope.kind === "project";
  if (state.topic !== "all" && !topics.includes(state.topic)) state.topic = "all";

  const page = node("section", "scope-page");
  const head = node("header", "scope-head");
  const copy = node("div", "scope-copy");
  const back = node("button", "back-button", returnsHome ? "Back to home" : "Back to spaces");
  back.type = "button";
  back.addEventListener("click", () => {
    if (returnsHome) state.view = "home";
    state.scope = null;
    state.topic = "all";
    state.filters = defaultFilters();
    state.visibleLimit = PAGE_SIZE;
    state.selectedResource = null;
    renderAtTop();
  });
  copy.append(
    back,
    node("p", "eyebrow", state.scope.kind === "project" ? "Active workspace" : "Purpose space"),
    node("h2", "", summary.name),
    node("p", "scope-summary", summary.objective || summary.description || "Resources grouped around this purpose.")
  );
  const facts = node("div", "scope-facts");
  facts.append(
    factChip(`${formatNumber(members.length)} resources`),
    factChip(`${formatNumber(members.filter(resource => !state.decisions[resource.resourceId]).length)} unqueued`)
  );
  head.append(copy, facts);
  page.append(head);

  if (topics.length) page.append(topicControl(topics));
  page.append(filterPanel(members));

  const filtered = filterResources(members).filter(resource =>
    state.topic === "all" || resourceTopics(resource).includes(state.topic)
  );
  page.append(resourceGallery(filtered, {
    label: state.topic === "all" ? "All resources" : state.topic,
    empty: "No resources match these topic and filter choices.",
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
  const browsers = unique(members.flatMap(resource => resource.tabs || []).map(tab => tab.browser).filter(Boolean));
  const groups = unique(members.flatMap(resource => resource.presentation?.groupTitles || []));
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
  const modes = [
    ["inbox", "Inbox", inboxResources().length],
    ["duplicates", "Exact duplicates", exactDuplicateResources().length],
    ["queued", "Queued", queuedResources().length]
  ];
  const page = node("section", "review-page");
  const head = node("header", "review-head");
  const copy = node("div");
  copy.append(
    node("p", "eyebrow", "Decision review"),
    node("h2", "", modes.find(item => item[0] === state.reviewMode)?.[1] || "Review"),
    node("p", "scope-summary", reviewDescription(state.reviewMode))
  );
  head.append(copy);
  if (state.reviewMode === "queued") {
    const exportButton = node("button", "primary-command", "Export queued decisions");
    exportButton.type = "button";
    exportButton.disabled = !queuedResources().length;
    exportButton.addEventListener("click", exportDecisions);
    head.append(exportButton);
  }
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

  let reviewResources = inboxResources();
  if (state.reviewMode === "duplicates") reviewResources = exactDuplicateResources();
  if (state.reviewMode === "queued") reviewResources = queuedResources();
  page.append(resourceGallery(reviewResources, {
    label: state.reviewMode === "duplicates"
      ? `${formatNumber(safeDuplicateCount())} safe close candidate${safeDuplicateCount() === 1 ? "" : "s"}`
      : `${formatNumber(reviewResources.length)} resource${reviewResources.length === 1 ? "" : "s"}`,
    empty: reviewEmptyMessage(state.reviewMode)
  }));
  elements.screen.replaceChildren(page);
}

function renderSearch() {
  const matches = resources.filter(matchesSearch).sort(resourceSort);
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
  heading.append(
    node("h3", "", options.label || "Resources"),
    node("span", "gallery-count", `${formatNumber(Math.min(items.length, state.visibleLimit))} of ${formatNumber(items.length)}`)
  );
  section.append(heading);
  if (!items.length) {
    section.append(emptyState(options.empty || "No resources in this view."));
    return section;
  }

  const grid = node("div", "resource-grid");
  for (const resource of items.slice(0, state.visibleLimit)) {
    grid.append(resourceCard(resource, options));
  }
  section.append(grid);
  if (state.visibleLimit < items.length) {
    const more = node("button", "show-more", `Show ${formatNumber(Math.min(PAGE_SIZE, items.length - state.visibleLimit))} more`);
    more.type = "button";
    more.addEventListener("click", () => {
      state.visibleLimit += PAGE_SIZE;
      render();
    });
    section.append(more);
  }
  return section;
}

function resourceCard(resource, options = {}) {
  const presentation = resource.presentation || {};
  const article = node("article", "resource-card");
  article.dataset.resourceId = resource.resourceId;

  const previewButton = node("button", "card-preview-button");
  previewButton.type = "button";
  previewButton.setAttribute("aria-label", `Open details for ${resourceDisplayTitle(resource)}`);
  previewButton.append(previewVisual(resource, "card", false));
  previewButton.addEventListener("click", event => openDetails(resource.resourceId, event.currentTarget));
  article.append(previewButton);

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
  article.append(body, decisionActions(resource, "card-actions"));
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
    frame.append(image);
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

function sourceFallback(resource) {
  const presentation = resource.presentation || {};
  const preview = presentation.preview || {};
  const fallback = node("div", "source-fallback");
  fallback.setAttribute("aria-hidden", "true");
  fallback.append(
    node("strong", "", preview.label || initials(presentation.source || resource.host || "Resource")),
    node("span", "", presentation.format || resource.kind || "Resource")
  );
  return fallback;
}

function resourceChips(resource, hideSpace = false) {
  const chips = node("div", "resource-chips");
  const space = resourceSpace(resource);
  const topics = resourceTopics(resource);
  const projects = resourceProjects(resource);
  const duplicate = duplicateSummary(resource);
  const queued = state.decisions[resource.resourceId];
  if (!hideSpace && space) chips.append(chip(space, "space-chip"));
  if (topics[0]) chips.append(chip(topics[0], "topic-chip"));
  else if (projects[0]) chips.append(chip(projects[0], "project-chip"));
  if (duplicate.sets) {
    const count = duplicate.safeCloseCandidates || duplicate.instances;
    const qualifier = duplicate.safeCloseCandidates ? "" : " protected";
    chips.append(chip(`${count}${qualifier} exact ${count === 1 ? "copy" : "copies"}`, "duplicate-chip"));
  }
  if (queued) chips.append(chip(decisionLabel(queued), `queued-chip queued-${queued}`));
  return chips;
}

function decisionActions(resource, className) {
  const wrapper = node("div", `decision-actions ${className}`);
  const selected = state.decisions[resource.resourceId] || "";
  wrapper.setAttribute("aria-label", `Queued decision for ${resourceDisplayTitle(resource)}`);
  for (const [status, label] of DECISIONS) {
    const button = node("button", selected === status ? `active action-${status}` : `action-${status}`, label);
    button.type = "button";
    button.setAttribute("aria-pressed", String(selected === status));
    button.title = "Queues this action only; the report does not change browser tabs.";
    button.addEventListener("click", () => setDecision(resource.resourceId, status));
    wrapper.append(button);
  }
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
  drawer.append(top, previewVisual(resource, "detail", true));

  const content = node("div", "drawer-content");
  const titleBlock = node("section", "detail-title");
  titleBlock.append(node("h2", "", resourceDisplayTitle(resource)));
  const openUrl = resource.openUrl || resource.canonicalUrl || "";
  if (/^(https?|file):/i.test(openUrl)) {
    const link = node("a", "open-source", "Open source");
    link.href = openUrl;
    link.target = "_blank";
    link.rel = "noreferrer";
    titleBlock.append(link);
  }
  content.append(titleBlock);

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
  content.append(instanceDisclosure(resource));
  drawer.append(content, decisionActions(resource, "drawer-actions"));
  backdrop.append(drawer);
  elements.drawerHost.replaceChildren(backdrop);
  requestAnimationFrame(() => close.focus({ preventScroll: true }));
}

function detailCategories(resource) {
  const values = [
    ["Space", resourceSpace(resource)],
    ["Topics", resourceTopics(resource).join(", ")],
    ["Projects", resourceProjects(resource).join(", ")],
    ["Browser groups", (resource.presentation?.groupTitles || []).join(", ")]
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
  const section = node("section", "detail-section");
  section.append(node("h3", "", "Metadata"));
  const facts = node("dl", "metadata-grid");
  facts.append(
    cue("Source", resource.presentation?.source || resource.host),
    cue("Format", resource.presentation?.format || resource.kind),
    cue("Intent", resource.presentation?.intent),
    cue("Open copies", String((resource.tabs || []).length)),
    cue("Browsers", unique((resource.tabs || []).map(tab => capitalize(tab.browser))).join(", ")),
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
  const details = document.createElement("details");
  details.className = "detail-disclosure";
  details.append(node("summary", "", `Tab instances (${(resource.tabs || []).length})`));
  const list = node("div", "instance-list");
  for (const tab of resource.tabs || []) {
    const item = node("div", "instance-row");
    item.append(
      node("strong", "", `${capitalize(tab.browser)}${tab.groupTitle ? ` / ${tab.groupTitle}` : ""}`),
      node("span", "", `Position ${Number(tab.position) + 1}${tab.pinned ? " / pinned" : ""}${tab.active ? " / active" : ""}${tab.audible ? " / audible" : ""}`)
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

function filterOptionLabel(label, value) {
  if (value === "all") return `All ${label.toLocaleLowerCase()}s`;
  if (value === "__ungrouped") return "No browser group";
  return capitalize(value);
}

function openScope(kind, id) {
  state.view = "spaces";
  state.search = "";
  elements.search.value = "";
  state.scope = { kind, id };
  state.topic = "all";
  state.filters = defaultFilters();
  state.visibleLimit = PAGE_SIZE;
  state.selectedResource = null;
  renderAtTop();
}

function selectedScopeSummary() {
  if (!state.scope) return null;
  const summaries = state.scope.kind === "project" ? projectSummaries : spaceSummaries;
  return summaries.find(summary => summary.id === state.scope.id) || null;
}

function resourcesForSummary(summary) {
  if (Array.isArray(summary.resourceIds)) {
    return summary.resourceIds.map(id => resourceById.get(id)).filter(Boolean).sort(resourceSort);
  }
  if (state.scope?.kind === "project") {
    return resources.filter(resource => resourceProjects(resource).includes(summary.name)).sort(resourceSort);
  }
  return resources.filter(resource => resourceSpace(resource) === summary.name).sort(resourceSort);
}

function scopeTopics(summary, members) {
  const named = (summary.topTopics || []).map(topic => topic.name).filter(Boolean);
  if (named.length) return named;
  const counts = new Map();
  for (const resource of members) {
    for (const topic of resourceTopics(resource)) counts.set(topic, (counts.get(topic) || 0) + 1);
  }
  return [...counts.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0])).slice(0, 6).map(item => item[0]);
}

function filterResources(items) {
  return items.filter(resource => {
    if (state.filters.format !== "all" && resource.presentation?.format !== state.filters.format) return false;
    if (state.filters.browser !== "all" && !(resource.tabs || []).some(tab => tab.browser === state.filters.browser)) return false;
    const groups = resource.presentation?.groupTitles || [];
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
  return resources.filter(resource => duplicateSummary(resource).sets > 0).sort((a, b) => {
    const count = duplicateSummary(b).safeCloseCandidates - duplicateSummary(a).safeCloseCandidates;
    return count || resourceSort(a, b);
  });
}

function queuedResources() {
  return resources.filter(resource => state.decisions[resource.resourceId]).sort(resourceSort);
}

function safeDuplicateCount() {
  const value = Number(data.inventory?.safeCloseCandidates);
  if (Number.isFinite(value)) return value;
  return resources.reduce((total, resource) => total + duplicateSummary(resource).safeCloseCandidates, 0);
}

function duplicateSummary(resource) {
  const duplicate = resource.presentation?.duplicates;
  if (duplicate && typeof duplicate === "object") {
    return {
      sets: Number(duplicate.sets || 0),
      instances: Number(duplicate.instances || 0),
      safeCloseCandidates: Number(duplicate.safeCloseCandidates || 0),
      protectedInstances: Number(duplicate.protectedInstances || 0)
    };
  }
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
  for (const tab of resource.tabs || []) {
    if (!/^https?:\/\//i.test(tab.url || "")) continue;
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
      safe: candidates.filter(tab => !tab.active && !tab.pinned && !tab.audible).length,
      protected: candidates.filter(tab => tab.active || tab.pinned || tab.audible).length
    });
  }
  return result;
}

function duplicateKeeperSort(a, b) {
  const rank = tab => [
    tab.active ? 0 : 1,
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

function resourceProjects(resource) {
  if (Array.isArray(resource.presentation?.projects) && resource.presentation.projects.length) return resource.presentation.projects;
  return (resource.collections || []).filter(value => value.kind === "project").map(value => value.name);
}

function resourceSort(a, b) {
  const queuedDifference = Number(Boolean(state.decisions[b.resourceId])) - Number(Boolean(state.decisions[a.resourceId]));
  if (queuedDifference) return queuedDifference;
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
    resource.presentation?.format,
    resource.presentation?.intent,
    resource.presentation?.contextCue,
    resourceSpace(resource),
    ...resourceTopics(resource),
    ...resourceProjects(resource),
    ...(resource.presentation?.groupTitles || [])
  ].join("\n").toLocaleLowerCase();
  return value.includes(state.search);
}

function reviewDescription(mode) {
  if (mode === "duplicates") return "Exact URL matches within the same browser window and group. Protected tabs remain visible and excluded from safe candidates.";
  if (mode === "queued") return "Actions selected in this report. Export preserves the existing annotation schema and does not mutate browser tabs.";
  return "Resources without a purpose space. Decide whether they belong in current work or can be closed after capture.";
}

function reviewEmptyMessage(mode) {
  if (mode === "duplicates") return "No exact duplicate sets are present in the current capture.";
  if (mode === "queued") return "No decisions have been queued in this report.";
  return "Every current resource has a purpose space.";
}

function openReview(mode) {
  state.view = "review";
  state.reviewMode = mode;
  state.search = "";
  elements.search.value = "";
  state.scope = null;
  state.visibleLimit = PAGE_SIZE;
  state.selectedResource = null;
  renderAtTop();
}

function setView(view) {
  state.view = ["home", "spaces", "review"].includes(view) ? view : "home";
  state.search = "";
  elements.search.value = "";
  state.selectedResource = null;
  state.visibleLimit = PAGE_SIZE;
  if (state.view !== "spaces") state.scope = null;
  if (state.view === "spaces") {
    state.scope = null;
    state.topic = "all";
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
}

function closeDetails() {
  const restore = state.lastFocus;
  state.selectedResource = null;
  state.lastFocus = null;
  renderDrawer();
  if (restore && restore.isConnected) requestAnimationFrame(() => restore.focus({ preventScroll: true }));
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
  const annotatedResources = Object.entries(state.decisions).map(([resourceId, status]) => ({ resourceId, status }));
  if (!annotatedResources.length) return;
  const payload = {
    schemaVersion: 1,
    generatedAt: new Date().toISOString(),
    sourceReportGeneratedAt: data.generatedAt,
    resources: annotatedResources
  };
  const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = "tabatlas-decisions.json";
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function decisionLabel(status) {
  return Object.fromEntries(DECISIONS)[status] || "Queued";
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

function totalTabCount() {
  return resources.reduce((total, resource) => total + (resource.tabs || []).length, 0);
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
