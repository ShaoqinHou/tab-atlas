const data = window.__TAB_ATLAS__ || { inventory: {}, resources: [], collections: [], tasks: [] };
const state = { search: "", collection: "all", browser: "all", group: "all", selected: null };

const app = document.getElementById("app");
app.innerHTML = `
  <header class="topbar">
    <div class="brand">
      <div class="brand-mark" aria-hidden="true">TA</div>
      <div><h1>TabAtlas</h1><p id="freshness"></p></div>
    </div>
    <input id="search" class="search" type="search" placeholder="Search titles, summaries, URLs, groups, and collections" aria-label="Search tab library">
  </header>
  <section id="metrics" class="metrics" aria-label="Library summary"></section>
  <div class="workspace">
    <aside class="sidebar">
      <p class="section-label">Library</p>
      <div id="libraryFilters" class="filter-list"></div>
      <p class="section-label">Browser groups</p>
      <div id="groupFilters" class="filter-list"></div>
      <p class="section-label">Collections</p>
      <div id="collectionFilters" class="filter-list"></div>
    </aside>
    <main class="content">
      <div class="content-head"><div><h2 id="viewTitle">All resources</h2><p id="resultCount"></p></div></div>
      <div id="resourceList" class="resource-list"></div>
    </main>
    <aside id="inspector" class="inspector" aria-label="Resource details" aria-live="polite" tabindex="-1"></aside>
  </div>`;

const elements = {
  freshness: document.getElementById("freshness"),
  search: document.getElementById("search"),
  metrics: document.getElementById("metrics"),
  libraryFilters: document.getElementById("libraryFilters"),
  groupFilters: document.getElementById("groupFilters"),
  collectionFilters: document.getElementById("collectionFilters"),
  viewTitle: document.getElementById("viewTitle"),
  resultCount: document.getElementById("resultCount"),
  resourceList: document.getElementById("resourceList"),
  inspector: document.getElementById("inspector")
};

elements.search.addEventListener("input", () => {
  state.search = elements.search.value.trim().toLocaleLowerCase();
  renderResources();
});

render();

function render() {
  renderFreshness();
  renderMetrics();
  renderFilters();
  renderResources();
}

function renderFreshness() {
  const captures = data.inventory.captures || [];
  if (!captures.length) {
    elements.freshness.textContent = "No capture imported yet";
    return;
  }
  elements.freshness.textContent = captures
    .map(item => `${capitalize(item.browser)} ${formatDate(item.capturedAt)}`)
    .join(" / ");
}

function renderMetrics() {
  const values = [
    [data.inventory.currentTabs || 0, "Tab instances"],
    [data.inventory.currentResources || 0, "Unique resources"],
    [data.inventory.duplicateTabInstances || 0, "Duplicate instances"],
    [data.inventory.groups || 0, "Browser groups"],
    [data.inventory.unclassifiedResources || 0, "Need review"],
    [data.inventory.openTasks || 0, "Open tasks"]
  ];
  elements.metrics.replaceChildren(...values.map(([value, label]) => {
    const metric = node("div", "metric");
    metric.append(node("strong", "", String(value)), node("span", "", label));
    return metric;
  }));
}

function renderFilters() {
  const browsers = countBy(data.resources, resource => [...new Set(resource.tabs.map(tab => tab.browser))]);
  const library = [
    { id: "all", label: "All resources", count: data.resources.length },
    { id: "duplicates", label: "Multiple instances", count: data.resources.filter(resource => resource.tabs.length > 1).length },
    { id: "open_tasks", label: "Open tasks", count: data.resources.filter(hasOpenTask).length },
    ...Object.entries(browsers).sort().map(([id, count]) => ({ id, label: capitalize(id), count }))
  ];
  elements.libraryFilters.replaceChildren(...library.map(item => filterButton(item, "browser")));

  const groupCounts = countBy(data.resources, resource => [...new Set(resource.tabs
    .filter(tab => tab.groupId !== null && tab.groupId !== "" && String(tab.groupId) !== "-1")
    .map(groupKey))]);
  const groups = [
    { id: "all", label: "Every group", count: data.resources.length },
    ...Object.entries(groupCounts)
      .sort((left, right) => groupLabel(left[0]).localeCompare(groupLabel(right[0])))
      .map(([id, count]) => ({ id, label: groupLabel(id), count }))
  ];
  elements.groupFilters.replaceChildren(...groups.map(item => filterButton(item, "group")));

  const collectionCounts = {};
  for (const resource of data.resources) {
    for (const collection of resource.collections) {
      collectionCounts[collection.id] = (collectionCounts[collection.id] || 0) + 1;
    }
  }
  const collections = [
    { id: "all", label: "Every collection", count: data.resources.length },
    { id: "unclassified", label: "Needs review", count: data.resources.filter(resource => !resource.collections.length).length },
    ...data.collections.map(item => ({ id: item.id, label: item.name, count: collectionCounts[item.id] || 0 }))
  ];
  elements.collectionFilters.replaceChildren(...collections.map(item => filterButton(item, "collection")));
}

function filterButton(item, type) {
  const button = node("button", `filter-button ${state[type] === item.id ? "active" : ""}`);
  button.type = "button";
  button.setAttribute("aria-pressed", String(state[type] === item.id));
  button.append(node("span", "", item.label), node("span", "count", String(item.count)));
  button.addEventListener("click", () => {
    const previous = button.parentElement.querySelector(".filter-button.active");
    if (previous && previous !== button) {
      previous.classList.remove("active");
      previous.setAttribute("aria-pressed", "false");
    }
    state[type] = item.id;
    button.classList.add("active");
    button.setAttribute("aria-pressed", "true");
    renderResources();
  });
  return button;
}

function renderResources() {
  const resources = data.resources.filter(matchesFilters);
  const selectedCollection = data.collections.find(item => item.id === state.collection);
  elements.viewTitle.textContent = viewTitle(selectedCollection);
  elements.resultCount.textContent = `${resources.length} resource${resources.length === 1 ? "" : "s"}`;

  if (!resources.length) {
    elements.resourceList.replaceChildren(node("div", "empty-state", "No resources match these filters."));
    renderInspector(null);
    return;
  }
  if (!resources.some(item => item.resourceId === state.selected)) state.selected = resources[0].resourceId;
  elements.resourceList.replaceChildren(...resources.map(resourceRow));
  renderInspector(resources.find(item => item.resourceId === state.selected) || resources[0]);
}

function matchesFilters(resource) {
  if (state.browser === "duplicates" && resource.tabs.length <= 1) return false;
  if (state.browser === "open_tasks" && !hasOpenTask(resource)) return false;
  if (!new Set(["all", "duplicates", "open_tasks"]).has(state.browser) && !resource.tabs.some(tab => tab.browser === state.browser)) return false;
  if (state.group !== "all" && !resource.tabs.some(tab => groupKey(tab) === state.group)) return false;
  if (state.collection === "unclassified" && resource.collections.length) return false;
  if (state.collection !== "all" && state.collection !== "unclassified" && !resource.collections.some(item => item.id === state.collection)) return false;
  if (!state.search) return true;
  const text = [
    resource.title,
    resource.canonicalUrl,
    resource.brief,
    resource.detail,
    resource.whyKept,
    resource.nextAction,
    ...resource.collections.map(item => item.name),
    ...resource.tabs.map(item => item.groupTitle),
    ...resource.tasks.flatMap(item => [item.title, item.notes || "", item.status])
  ].join("\n").toLocaleLowerCase();
  return text.includes(state.search);
}

function resourceRow(resource) {
  const button = node("button", `resource-row ${resource.resourceId === state.selected ? "selected" : ""}`);
  button.type = "button";
  button.setAttribute("aria-pressed", String(resource.resourceId === state.selected));
  button.setAttribute("aria-controls", "inspector");
  const mark = node("span", "site-mark", initials(resource.host || resource.kind));
  mark.style.background = colorFor(resource.host || resource.kind);

  const identity = node("div");
  identity.append(node("p", "resource-title", resource.title || "Untitled resource"), node("p", "resource-url", resource.displayUrl));
  const brief = node("div", `brief ${resource.brief ? "" : "empty"}`, resource.brief || "Needs a concise summary");
  const meta = node("div", "row-meta");
  const badges = node("div", "badges");
  badges.append(node("span", "badge", `${resource.tabs.length} tab${resource.tabs.length === 1 ? "" : "s"}`));
  if (resource.nextAction && resource.nextAction !== "none") badges.append(node("span", "badge action", "Action"));
  else if (resource.status === "saved") badges.append(node("span", "badge saved", "Saved"));
  meta.append(badges, node("span", "", resource.kind.replaceAll("_", " ")));
  button.append(mark, identity, brief, meta);
  button.addEventListener("click", () => {
    const previous = elements.resourceList.querySelector(".resource-row.selected");
    if (previous && previous !== button) {
      previous.classList.remove("selected");
      previous.setAttribute("aria-pressed", "false");
    }
    state.selected = resource.resourceId;
    button.classList.add("selected");
    button.setAttribute("aria-pressed", "true");
    renderInspector(resource);
    elements.inspector.focus({ preventScroll: true });
    if (window.matchMedia("(max-width: 900px)").matches) {
      elements.inspector.scrollIntoView({ behavior: "smooth", block: "start" });
    }
  });
  return button;
}

function renderInspector(resource) {
  if (!resource) {
    elements.inspector.replaceChildren(node("div", "inspector-empty", "Select a resource to inspect it."));
    return;
  }
  const fragment = document.createDocumentFragment();
  fragment.append(node("h2", "", resource.title || "Untitled resource"));
  const openUrl = resource.openUrl || resource.canonicalUrl;
  if (/^(https?|file):/i.test(openUrl)) {
    const link = node("a", "inspector-url", resource.displayUrl);
    link.href = openUrl;
    link.target = "_blank";
    link.rel = "noreferrer";
    fragment.append(link);
  } else {
    fragment.append(node("span", "inspector-url", resource.displayUrl));
  }

  const acceptedCollections = resource.collections.filter(collection => Boolean(collection.accepted));
  const suggestedCollections = resource.collections.filter(collection => !collection.accepted);
  if (acceptedCollections.length) fragment.append(collectionSection("Collections", acceptedCollections, "saved"));
  if (suggestedCollections.length) fragment.append(collectionSection("Suggested collections", suggestedCollections, "suggested"));
  fragment.append(textSection("Brief", resource.brief));
  fragment.append(textSection("Detail", resource.detail));
  fragment.append(textSection("Why it may have been kept", resource.whyKept));
  fragment.append(textSection("Next action", resource.nextAction));

  const sources = node("div", "source-list");
  for (const tab of resource.tabs) {
    const item = node("div", "source-item");
    item.append(
      node("strong", "", `${capitalize(tab.browser)}${tab.groupTitle ? ` / ${tab.groupTitle}` : ""}`),
      node("span", "", `Window ${tab.windowId || "?"}, position ${Number(tab.position) + 1}${tab.pinned ? ", pinned" : ""}${tab.active ? ", active" : ""}`)
    );
    sources.append(item);
  }
  fragment.append(section("Current tab instances", sources));

  if (resource.tasks.length) {
    const tasks = node("div", "task-list");
    for (const task of resource.tasks) {
      const item = node("div", "task-item");
      item.append(node("strong", "", task.title), node("span", "", `${capitalize(task.status)}${task.notes ? ` / ${task.notes}` : ""}`));
      tasks.append(item);
    }
    fragment.append(section("Tasks", tasks));
  }
  elements.inspector.replaceChildren(fragment);
}

function textSection(title, value) {
  return section(title, node("p", value ? "" : "missing", value || "Not recorded yet."));
}

function section(title, content) {
  const wrapper = node("section", "detail-section");
  wrapper.append(node("h3", "", title), content);
  return wrapper;
}

function collectionSection(title, collections, badgeClass) {
  const badges = node("div", "badges");
  for (const collection of collections) badges.append(node("span", `badge ${badgeClass}`, collection.name));
  return section(title, badges);
}

function node(tag, className = "", text = "") {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== "") element.textContent = text;
  return element;
}

function countBy(items, keyFn) {
  const counts = {};
  for (const item of items) {
    const keys = keyFn(item);
    for (const key of keys) counts[key] = (counts[key] || 0) + 1;
  }
  return counts;
}

function groupKey(tab) {
  const name = tab.groupTitle || `Unnamed group ${tab.groupId}`;
  return JSON.stringify([tab.browser, String(tab.windowId || ""), String(tab.groupId || ""), name]);
}

function groupLabel(key) {
  try {
    const [browser, windowId, _groupId, name] = JSON.parse(key);
    return `${capitalize(browser)} / ${name || "Unnamed group"} / window ${windowId || "?"}`;
  } catch {
    return "Unknown browser group";
  }
}

function hasOpenTask(resource) {
  return resource.tasks.some(task => task.status === "open");
}

function viewTitle(selectedCollection) {
  if (state.group !== "all") return groupLabel(state.group);
  if (state.collection === "unclassified") return "Needs review";
  if (selectedCollection) return selectedCollection.name;
  if (state.browser === "duplicates") return "Multiple tab instances";
  if (state.browser === "open_tasks") return "Open tasks";
  if (state.browser !== "all") return `${capitalize(state.browser)} resources`;
  return "All resources";
}

function initials(value) {
  const parts = String(value || "?").split(/[.\-_\s]+/).filter(Boolean);
  return parts.slice(0, 2).map(part => part[0]).join("").toUpperCase() || "?";
}

function colorFor(value) {
  const palette = ["#345c72", "#28745d", "#76538b", "#936029", "#8a4652", "#496b3f", "#3f608f"];
  let hash = 0;
  for (const char of String(value)) hash = ((hash << 5) - hash + char.charCodeAt(0)) | 0;
  return palette[Math.abs(hash) % palette.length];
}

function capitalize(value) {
  const text = String(value || "");
  return text.charAt(0).toUpperCase() + text.slice(1);
}

function formatDate(value) {
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? value : date.toLocaleString();
}
