// Sole owner of mutable state shared across report features.
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
  tabClosurePolls: new Map(),
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

function replaceCatalogSnapshot(snapshot, options = {}) {
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
  if (options.preserveViewport) renderPreservingViewport();
  else render();
}

function applyDiscoveryDecisionLocally(decision, resourceIds, inventory) {
  const selected = new Set(resourceIds);
  const changed = trackedResources.filter(resource => selected.has(resource.resourceId));
  const changedAt = new Date().toISOString();
  for (const resource of changed) {
    resource.libraryState = decision === "accept" ? "accepted" : "dismissed";
    if (decision === "accept") {
      resource.acceptedAt = changedAt;
      resource.dismissedAt = "";
    } else {
      resource.dismissedAt = changedAt;
    }
  }
  discoveries = discoveries.filter(resource => !selected.has(resource.resourceId));
  dismissed = dismissed.filter(resource => !selected.has(resource.resourceId));
  resources = resources.filter(resource => !selected.has(resource.resourceId));
  if (decision === "accept") resources = [...changed, ...resources];
  else dismissed = [...changed, ...dismissed];
  allResources = [...discoveries, ...resources];
  trackedResources = [...allResources, ...dismissed];
  resourceById = new Map(trackedResources.map(resource => [resource.resourceId, resource]));
  data.resources = resources;
  data.discoveries = discoveries;
  data.dismissed = dismissed;
  if (inventory && typeof inventory === "object") data.inventory = inventory;
}
