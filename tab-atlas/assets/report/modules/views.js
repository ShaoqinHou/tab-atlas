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
    ["dismissed", "Dismissed", dismissed.length],
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
  if (state.reviewMode === "dismissed") reviewResources = dismissed;
  if (state.reviewMode === "duplicates") reviewResources = exactDuplicateResources();
  if (state.reviewMode === "open") reviewResources = openResources;
  let galleryLabel = `${formatNumber(reviewResources.length)} resource${reviewResources.length === 1 ? "" : "s"}`;
  if (state.reviewMode === "discoveries") {
    galleryLabel = `${formatNumber(reviewResources.length)} awaiting approval`;
  } else if (state.reviewMode === "dismissed") {
    galleryLabel = `${formatNumber(reviewResources.length)} dismissed resource${reviewResources.length === 1 ? "" : "s"}`;
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
    label = `Close ${formatNumber(openTabs)} reviewed tabs`;
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
      ? (workspace.interactive
          ? "Adds this reviewed batch to the local library. Browser tabs are unchanged."
          : "Downloads a request to add this reviewed batch. Browser tabs are unchanged.")
      : (mode === "open" && dismissedOpen
          ? `${formatNumber(dismissedOpen)} dismissed resource${dismissedOpen === 1 ? "" : "s"} will be discarded; accepted resources remain in the library. ${REQUEST_SAFETY_TEXT}`
          : REQUEST_SAFETY_TEXT)
  );
  note.id = "reviewActionSafety";
  button.type = "button";
  button.disabled = disabled;
  button.title = mode === "discoveries" && workspace.interactive
    ? "Updates the local library only; browser tabs are unchanged."
    : "Downloads a privacy-safe action request; it does not close tabs directly.";
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
