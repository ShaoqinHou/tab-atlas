// Purpose-space and source-directory renderers.
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
