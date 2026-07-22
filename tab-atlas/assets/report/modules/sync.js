const ACTIVE_BROWSER_SYNC_PHASES = new Set([
  "starting",
  "waiting_for_extension",
  "publishing"
]);
const TERMINAL_BROWSER_SYNC_PHASES = new Set(["complete", "partial"]);

async function initializeBrowserSync() {
  if (!workspace.interactive) return;
  try {
    const status = await workspaceRequest("/api/v1/browser-sync");
    workspace.browserSyncSupported = true;
    elements.syncControl.hidden = false;
    applyBrowserSyncStatus(status, { refreshTerminal: true });
  } catch (_error) {
    workspace.browserSyncSupported = false;
    elements.syncControl.hidden = true;
  }
}

async function startBrowserSync(browsers = ["chrome", "edge"]) {
  if (!workspace.interactive || !workspace.browserSyncSupported) return;
  clearBrowserSyncPoll();
  workspace.browserSyncTerminalKey = "";
  elements.syncNow.disabled = true;
  elements.syncStatus.textContent = "Starting browser sync";
  elements.syncControl.setAttribute("aria-busy", "true");
  try {
    const status = await workspaceRequest("/api/v1/browser-sync", {
      method: "POST",
      json: { browsers }
    });
    applyBrowserSyncStatus(status, { refreshTerminal: true });
  } catch (error) {
    elements.syncNow.disabled = false;
    elements.syncControl.removeAttribute("aria-busy");
    elements.syncStatus.textContent = "Sync could not start";
    elements.syncStatus.title = String(error.message || "Browser sync failed");
    elements.actionStatus.textContent = "Browser sync could not start. Your catalog was not changed.";
  }
}

async function pollBrowserSync() {
  workspace.browserSyncPoll = null;
  if (!isBrowserSyncActive(workspace.browserSync.phase)) return;
  try {
    const status = await workspaceRequest("/api/v1/browser-sync");
    applyBrowserSyncStatus(status, { refreshTerminal: true });
  } catch (_error) {
    elements.syncStatus.textContent = "Sync status interrupted; retrying";
    scheduleBrowserSyncPoll(2200);
  }
}

function applyBrowserSyncStatus(payload, options = {}) {
  const status = normalizeBrowserSyncStatus(payload);
  workspace.browserSync = status;
  renderBrowserSyncStatus();

  if (isBrowserSyncActive(status.phase)) {
    scheduleBrowserSyncPoll();
    return;
  }

  clearBrowserSyncPoll();
  if (!options.refreshTerminal || !TERMINAL_BROWSER_SYNC_PHASES.has(status.phase)) return;
  const terminalKey = browserSyncTerminalKey(status);
  if (terminalKey === workspace.browserSyncTerminalKey) return;
  workspace.browserSyncTerminalKey = terminalKey;
  refreshCatalogSnapshot({ announce: true }).catch(error => {
    workspace.browserSyncTerminalKey = "";
    elements.syncStatus.textContent = "Synced; catalog refresh failed";
    elements.syncStatus.title = String(error.message || "Catalog refresh failed");
    elements.actionStatus.textContent = "Browser sync finished, but the updated catalog could not be loaded.";
  });
}

function normalizeBrowserSyncStatus(payload) {
  const source = payload?.sync && typeof payload.sync === "object" ? payload.sync : payload;
  const phaseValue = String(source?.phase || "idle");
  const phase = phaseValue === "completed" ? "complete" : phaseValue;
  const browsers = source?.browsers && typeof source.browsers === "object"
    ? source.browsers
    : {};
  return {
    phase,
    browsers,
    newResources: Math.max(0, Number(source?.newResources) || 0),
    pendingDiscoveries: Math.max(0, Number(source?.pendingDiscoveries) || 0),
    error: source?.error ? String(source.error) : ""
  };
}

function renderBrowserSyncStatus() {
  const status = workspace.browserSync;
  const active = isBrowserSyncActive(status.phase);
  const labels = {
    idle: "Ready",
    starting: "Starting browsers",
    waiting_for_extension: "Waiting for extensions",
    publishing: "Updating catalog",
    complete: browserSyncResultLabel(status, "Synced"),
    partial: browserSyncResultLabel(status, "Partially synced"),
    failed: "Sync failed"
  };
  elements.syncNow.disabled = active;
  elements.syncNow.textContent = active ? "Syncing" : "Sync now";
  elements.syncStatus.textContent = labels[status.phase] || "Sync unavailable";
  elements.syncStatus.title = browserSyncDetail(status);
  elements.syncControl.dataset.phase = status.phase;
  if (active) elements.syncControl.setAttribute("aria-busy", "true");
  else elements.syncControl.removeAttribute("aria-busy");
}

function browserSyncResultLabel(status, prefix) {
  const additions = status.newResources
    ? `${formatNumber(status.newResources)} new`
    : "no new tabs";
  const pending = status.pendingDiscoveries
    ? `, ${formatNumber(status.pendingDiscoveries)} to review`
    : "";
  return `${prefix}: ${additions}${pending}`;
}

function browserSyncDetail(status) {
  const browserDetails = Object.entries(status.browsers || {}).map(([browser, value]) => {
    const browserStatus = typeof value === "string" ? value : String(value?.state || value?.status || value?.phase || "unknown");
    return `${capitalize(browser)}: ${browserStatus.replaceAll("_", " ")}`;
  });
  if (status.error) browserDetails.push(status.error);
  return browserDetails.join("; ");
}

function browserSyncTerminalKey(status) {
  const browserStates = Object.entries(status.browsers || {})
    .map(([browser, value]) => [browser, typeof value === "string" ? value : value?.state || value?.status || value?.phase || ""])
    .sort(([left], [right]) => left.localeCompare(right));
  return JSON.stringify([
    status.phase,
    status.newResources,
    status.pendingDiscoveries,
    browserStates
  ]);
}

function isBrowserSyncActive(phase) {
  return ACTIVE_BROWSER_SYNC_PHASES.has(String(phase || ""));
}

function scheduleBrowserSyncPoll(delay = 1200) {
  clearBrowserSyncPoll();
  if (!isBrowserSyncActive(workspace.browserSync.phase)) return;
  workspace.browserSyncPoll = window.setTimeout(pollBrowserSync, delay);
}

function clearBrowserSyncPoll() {
  if (workspace.browserSyncPoll === null) return;
  window.clearTimeout(workspace.browserSyncPoll);
  workspace.browserSyncPoll = null;
}

async function refreshCatalogSnapshot(options = {}) {
  if (!workspace.interactive) return null;
  const payload = await workspaceRequest("/api/v1/catalog");
  const snapshot = payload?.catalog || payload?.snapshot || payload;
  if (!snapshot || !Array.isArray(snapshot.resources) || !Array.isArray(snapshot.discoveries)) {
    throw new Error("The catalog endpoint returned an invalid snapshot.");
  }
  replaceCatalogSnapshot(snapshot);
  if (options.announce) {
    elements.actionStatus.textContent = "Browser sync finished and the catalog was updated.";
  }
  return snapshot;
}
