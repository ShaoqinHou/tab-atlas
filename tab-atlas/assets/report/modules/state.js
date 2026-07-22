let data = window.__TAB_ATLAS__ || {
  inventory: {},
  resources: [],
  discoveries: [],
  dismissed: [],
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
let hasSpaceContract = Object.prototype.hasOwnProperty.call(data, "spaceSummaries");
let resources = Array.isArray(data.resources) ? data.resources : [];
let discoveries = Array.isArray(data.discoveries) ? data.discoveries : [];
let dismissed = Array.isArray(data.dismissed) ? data.dismissed : [];
let allResources = [...discoveries, ...resources];
let trackedResources = [...allResources, ...dismissed];
let resourceById = new Map(trackedResources.map(resource => [resource.resourceId, resource]));
let spaceSummaries = hasSpaceContract
  ? (data.spaceSummaries || [])
  : (data.collectionSummaries || []);
let projectSummaries = data.projectSummaries || [];
let actionListSummaries = data.actionListSummaries || [];
let sourceSummaries = Array.isArray(data.sourceSummaries) ? data.sourceSummaries : [];
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
  previousView: null,
  browserSync: {
    phase: "idle",
    browsers: {},
    newResources: 0,
    pendingDiscoveries: discoveries.length,
    error: ""
  },
  browserSyncSupported: false,
  browserSyncPoll: null,
  browserSyncTerminalKey: "",
  catalogRevision: String(data.revision || "")
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
  filters: { format: "all", browser: "all", group: "all" },
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
    <div id="syncControl" class="sync-control" hidden>
      <button id="syncNow" class="sync-command" type="button">Sync now</button>
      <span id="syncStatus" class="sync-status" role="status" aria-live="polite">Ready</span>
    </div>
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
  syncControl: document.getElementById("syncControl"),
  syncNow: document.getElementById("syncNow"),
  syncStatus: document.getElementById("syncStatus"),
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

function replaceCatalogSnapshot(snapshot) {
  const next = snapshot && typeof snapshot === "object" ? snapshot : {};
  const nextResources = Array.isArray(next.resources) ? next.resources : [];
  const nextDiscoveries = Array.isArray(next.discoveries) ? next.discoveries : [];
  const nextDismissed = Array.isArray(next.dismissed) ? next.dismissed : [];
  const nextAllResources = [...nextDiscoveries, ...nextResources];
  const nextTrackedResources = [...nextAllResources, ...nextDismissed];
  const nextResourceById = new Map(
    nextTrackedResources.map(resource => [resource.resourceId, resource])
  );
  const nextHasSpaceContract = Object.prototype.hasOwnProperty.call(next, "spaceSummaries");

  data = next;
  resources = nextResources;
  discoveries = nextDiscoveries;
  dismissed = nextDismissed;
  allResources = nextAllResources;
  trackedResources = nextTrackedResources;
  resourceById = nextResourceById;
  hasSpaceContract = nextHasSpaceContract;
  spaceSummaries = nextHasSpaceContract
    ? (Array.isArray(next.spaceSummaries) ? next.spaceSummaries : [])
    : (Array.isArray(next.collectionSummaries) ? next.collectionSummaries : []);
  projectSummaries = Array.isArray(next.projectSummaries) ? next.projectSummaries : [];
  actionListSummaries = Array.isArray(next.actionListSummaries) ? next.actionListSummaries : [];
  sourceSummaries = Array.isArray(next.sourceSummaries) ? next.sourceSummaries : [];
  workspace.catalogRevision = String(next.revision || "");
  window.__TAB_ATLAS__ = next;

  if (state.selectedResource && !resourceById.has(state.selectedResource)) {
    state.selectedResource = null;
    state.lastFocus = null;
  }
  renderFreshness();
  render();
}
