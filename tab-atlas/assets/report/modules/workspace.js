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
    await initializeBrowserSync();
  } catch (_error) {
    workspace.interactive = false;
    elements.agentToggle.hidden = true;
    elements.syncControl.hidden = true;
    clearBrowserSyncPoll();
  }
}

function mergeWorkspaceSession(session) {
  workspace.agent = session.agent || {};
  organizationBatches = Array.isArray(session.organizationBatches)
    ? session.organizationBatches
    : organizationBatches;
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
