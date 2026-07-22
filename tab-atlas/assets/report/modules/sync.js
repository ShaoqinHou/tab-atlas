const ACTIVE_BROWSER_SYNC_PHASES = new Set([
  "starting",
  "waiting_for_extension",
  "preparing_review",
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
  elements.syncDetail.textContent = "Waiting for the paired browser extensions.";
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
    elements.syncDetail.textContent = "No browser data or library decisions were changed.";
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
    elements.syncDetail.textContent = "The catalog remains available while status reconnects.";
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
    elements.syncDetail.textContent = "The browser capture is safe; retry Sync now to refresh this view.";
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
  const previews = source?.previews && typeof source.previews === "object"
    ? source.previews
    : {};
  return {
    phase,
    browsers,
    startedAt: String(source?.startedAt || ""),
    completedAt: String(source?.completedAt || ""),
    newResources: Math.max(0, Number(source?.newResources) || 0),
    pendingDiscoveries: Math.max(0, Number(source?.pendingDiscoveries) || 0),
    previews: {
      state: String(previews.state || "idle"),
      eligible: Math.max(0, Number(previews.eligible) || 0),
      alreadyCached: Math.max(0, Number(previews.alreadyCached) || 0),
      prepared: Math.max(0, Number(previews.prepared) || 0),
      motionPrepared: Math.max(0, Number(previews.motionPrepared) || 0),
      failed: Math.max(0, Number(previews.failed) || 0),
      deferred: Math.max(0, Number(previews.deferred) || 0),
      error: previews.error ? String(previews.error) : ""
    },
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
    preparing_review: "Preparing review previews",
    publishing: "Updating catalog",
    complete: browserCaptureLabel(status) || "Browsers synced",
    partial: `Partial · ${browserCaptureLabel(status) || "browser response incomplete"}`,
    failed: "Sync failed"
  };
  elements.syncNow.disabled = active;
  elements.syncNow.textContent = active ? "Syncing" : "Sync now";
  elements.syncStatus.textContent = labels[status.phase] || "Sync unavailable";
  elements.syncDetail.textContent = browserSyncSummary(status);
  elements.syncControl.title = browserSyncDetail(status);
  elements.syncControl.dataset.phase = status.phase;
  if (active) elements.syncControl.setAttribute("aria-busy", "true");
  else elements.syncControl.removeAttribute("aria-busy");
}

function browserCaptureLabel(status) {
  return Object.entries(status.browsers || {})
    .map(([browser, value]) => {
      const stateValue = typeof value === "string" ? value : String(value?.state || "unknown");
      if (stateValue === "captured") return `${capitalize(browser)} ${formatNumber(value?.tabs || 0)}`;
      if (stateValue === "unavailable") return `${capitalize(browser)} unavailable`;
      if (stateValue === "timed_out") return `${capitalize(browser)} timed out`;
      return `${capitalize(browser)} waiting`;
    })
    .join(" · ");
}

function browserSyncSummary(status) {
  if (status.phase === "idle") return "No browser scan has run in this workspace.";
  if (status.phase === "starting" || status.phase === "waiting_for_extension") {
    return "Passive extensions may take up to 30 seconds to wake; the library remains usable.";
  }
  if (status.phase === "preparing_review") return "Tabs captured; caching safe public review images.";
  if (status.phase === "publishing") return "Review cards are being replaced with the new catalog snapshot.";
  if (status.phase === "failed") return status.error || browserCaptureLabel(status) || "No browser answered.";
  const parts = [
    status.newResources ? `${formatNumber(status.newResources)} new` : "no new pages",
    `${formatNumber(status.pendingDiscoveries)} to review`
  ];
  if (status.previews.prepared) parts.push(`${formatNumber(status.previews.prepared)} previews prepared`);
  const unavailablePreviews = status.previews.failed + status.previews.deferred;
  if (unavailablePreviews) parts.push(`${formatNumber(unavailablePreviews)} previews unavailable`);
  if (status.completedAt) parts.push(formatDate(status.completedAt));
  return parts.join(" · ");
}

function browserSyncDetail(status) {
  const browserDetails = Object.entries(status.browsers || {}).map(([browser, value]) => {
    const browserStatus = typeof value === "string" ? value : String(value?.state || value?.status || value?.phase || "unknown");
    const tabs = browserStatus === "captured" ? `, ${formatNumber(value?.tabs || 0)} tabs` : "";
    return `${capitalize(browser)}: ${browserStatus.replaceAll("_", " ")}${tabs}`;
  });
  if (status.previews.state !== "idle") {
    browserDetails.push(
      `Previews: ${status.previews.state}, ${formatNumber(status.previews.prepared)} prepared, ${formatNumber(status.previews.failed)} failed now, ${formatNumber(status.previews.deferred)} retry deferred`
    );
  }
  if (status.completedAt) browserDetails.push(`Finished: ${formatDate(status.completedAt)}`);
  if (status.previews.error) browserDetails.push(`Preview preparation: ${status.previews.error}`);
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
    status.previews.prepared,
    status.previews.failed,
    status.previews.deferred,
    status.completedAt,
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
