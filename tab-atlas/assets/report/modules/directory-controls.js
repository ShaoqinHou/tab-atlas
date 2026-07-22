// Reusable directory cards, headings, selects, and library-lens controls.
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
