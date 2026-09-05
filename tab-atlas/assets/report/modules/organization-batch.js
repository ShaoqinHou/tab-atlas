// Whole-cohort organization review and explicit proposal decisions.
function latestOrganizationBatch(scope = state.organizationScope) {
  const matches = organizationBatches.filter(batch => batch.scope === scope);
  if (!matches.length) return null;
  if (scope === "library") {
    // A small follow-up batch is a delta, not a replacement for the last
    // whole-library pass. Prefer the broadest cohort so the UI describes the
    // analysis that actually covers the library; createdAt breaks ties when
    // two full passes cover the same number of resources.
    return matches
      .slice()
      .sort((left, right) => {
        const targetDelta = Number(right.targetCount || 0) - Number(left.targetCount || 0);
        if (targetDelta) return targetDelta;
        return String(right.createdAt || "").localeCompare(String(left.createdAt || ""));
      })[0];
  }
  return matches[0];
}

function organizationBatchItem(resource, scope = null) {
  const batch = latestOrganizationBatch(scope || (state.reviewMode === "organization" ? state.organizationScope : "unorganized"));
  if (!batch || !resource) return null;
  return (batch.items || []).find(item => item.resourceId === resource.resourceId) || null;
}

function pendingOrganizationProposalCount(scope = "") {
  const batches = scope ? [latestOrganizationBatch(scope)].filter(Boolean) : [latestOrganizationBatch("unorganized"), latestOrganizationBatch("library")].filter(Boolean);
  if (scope) return batches.reduce((total, batch) => total + Number(batch.counts?.proposed || 0), 0);
  return new Set(
    batches.flatMap(batch => (batch.items || []).filter(item => item.effectiveState === "proposed").map(item => item.resourceId))
  ).size;
}

function organizationAttentionCount() {
  const batch = latestOrganizationBatch("unorganized");
  if (!batch) return inboxResources().length;
  const inboxIds = new Set(inboxResources().map(resource => resource.resourceId));
  return (batch.items || []).filter(item =>
    inboxIds.has(item.resourceId) && ["proposed", "needsContext", "stale"].includes(item.effectiveState)
  ).length;
}

function unorganizedAnalysisStatus() {
  const batch = latestOrganizationBatch("unorganized");
  if (!batch) return "In the library; not analyzed";
  const proposed = Number(batch.counts?.proposed || 0);
  const context = Number(batch.counts?.needsContext || 0);
  if (proposed) return `${formatNumber(proposed)} suggestions ready`;
  if (context) return `Analyzed; ${formatNumber(context)} need context`;
  return "Analyzed; no action needed";
}

function organizationReviewItems(scope = state.organizationScope, itemState = state.organizationItemState) {
  const batch = latestOrganizationBatch(scope);
  if (!batch) return [];
  const matches = item => {
    if (itemState === "reviewed") return ["accepted", "rejected", "superseded"].includes(item.effectiveState);
    return item.effectiveState === itemState;
  };
  return (batch.items || []).filter(matches);
}

function organizationReviewResources(scope = state.organizationScope, itemState = state.organizationItemState) {
  return organizationReviewItems(scope, itemState)
    .map(item => resourceById.get(item.resourceId))
    .filter(Boolean)
    .sort(resourceSort);
}

function organizationAwaitingAnalysisCount(scope = state.organizationScope) {
  const batch = latestOrganizationBatch(scope);
  const cohort = scope === "library" ? resources : inboxResources();
  if (!batch) return cohort.length;
  // A complete pass may be followed by a small delta pass. Count coverage
  // across both so the UI does not call already-reviewed resources "new" just
  // because the broad pass is the one selected for display.
  const analyzed = new Set(
    organizationBatches
      .filter(candidate => candidate.scope === scope)
      .flatMap(candidate => candidate.items || [])
      .map(item => item.resourceId)
  );
  return cohort.filter(resource => !analyzed.has(resource.resourceId)).length;
}

function organizationScopeLabel(scope) {
  return scope === "library" ? "Whole library" : "Saved, not organized";
}

function organizationBatchPanel() {
  const panel = node("section", "organization-workspace");
  const switcher = node("div", "organization-scope-switcher");
  for (const scope of ["unorganized", "library"]) {
    const batch = latestOrganizationBatch(scope);
    const button = node("button", state.organizationScope === scope ? "active" : "");
    button.type = "button";
    button.setAttribute("aria-pressed", String(state.organizationScope === scope));
    button.append(
      node("strong", "", organizationScopeLabel(scope)),
      node("span", "", batch
        ? `${formatNumber(batch.analyzedCount)} analyzed · ${formatNumber(batch.counts?.proposed || 0)} proposed`
        : "Not analyzed yet")
    );
    button.addEventListener("click", () => {
      state.organizationScope = scope;
      state.visibleLimit = PAGE_SIZE;
      renderAtTop();
    });
    switcher.append(button);
  }
  panel.append(switcher);

  const batch = latestOrganizationBatch();
  if (!batch) {
    panel.append(node("div", "organization-empty", "No global analysis has been staged for this scope. Saved resources are still in the library; this means Codex has not yet produced a reviewable cohort plan."));
    return panel;
  }

  const counts = batch.counts || {};
  const awaiting = organizationAwaitingAnalysisCount();
  const summary = node("div", "organization-summary");
  const copy = node("div", "organization-summary-copy");
  copy.append(
    node("p", "eyebrow", batch.strategy?.mode === "best_effort" ? "Codex best judgment" : "Whole-cohort analysis"),
    node("h3", "", `${formatNumber(batch.analyzedCount)} of ${formatNumber(batch.targetCount)} analyzed together`),
    node("p", "", batch.strategy?.summary || "Resources were compared as one cohort to identify reusable purpose and usage patterns.")
  );
  const facts = node("dl", "organization-facts");
  const factValues = [
    ["Suggestions ready", counts.proposed || 0],
    ["Needs your context", counts.needsContext || 0],
    [batch.scope === "unorganized" ? "Already organized" : "No change suggested", batch.scope === "unorganized" ? Number(counts.accepted || 0) + Number(counts.superseded || 0) : counts.unchanged || 0],
    ["New since analysis", awaiting]
  ];
  if (batch.scope === "unorganized") factValues.splice(3, 0, ["Left unorganized", counts.unchanged || 0]);
  for (const [label, value] of factValues) {
    const fact = node("div");
    fact.append(node("dt", "", label), node("dd", "", formatNumber(value)));
    facts.append(fact);
  }
  summary.append(copy, facts);
  panel.append(summary);

  const itemModes = node("div", "organization-item-modes");
  for (const [itemState, label, count] of [
    ["proposed", "Suggestions", counts.proposed || 0],
    ["needsContext", "Needs your context", counts.needsContext || 0],
    ["unchanged", batch.scope === "unorganized" ? "Left unorganized" : "No change", counts.unchanged || 0],
    ["reviewed", "Reviewed", Number(counts.accepted || 0) + Number(counts.rejected || 0) + Number(counts.superseded || 0)]
  ]) {
    const button = node("button", state.organizationItemState === itemState ? "active" : "");
    button.type = "button";
    button.setAttribute("aria-pressed", String(state.organizationItemState === itemState));
    button.append(node("span", "", label), node("strong", "", formatNumber(count)));
    button.addEventListener("click", () => {
      state.organizationItemState = itemState;
      state.visibleLimit = PAGE_SIZE;
      renderPreservingViewport();
    });
    itemModes.append(button);
  }
  panel.append(itemModes);

  const groups = Array.isArray(batch.strategy?.groups) ? batch.strategy.groups.slice(0, 12) : [];
  if (groups.length) {
    const patterns = node("div", "organization-patterns");
    patterns.append(
      node("strong", "", "Patterns found across this scope"),
      node("p", "", "Space → Topic → Focus is the main purpose path. Projects and action lists remain optional overlays.")
    );
    const list = node("div", "organization-pattern-list");
    for (const group of groups) {
      const path = Array.isArray(group.path) && group.path.length ? group.path.join(" → ") : "Needs context";
      const row = node("div", "organization-pattern-row");
      row.append(node("span", "", path), node("strong", "", formatNumber(group.count || 0)));
      if (group.reason) row.title = group.reason;
      list.append(row);
    }
    patterns.append(list);
    panel.append(patterns);
  }
  return panel;
}

async function requestOrganizationProposalDecision(item, decision, button = null) {
  if (!workspace.interactive || !item?.proposalId) return;
  if (button) button.disabled = true;
  setActionFeedback(decision === "accept" ? "Applying this organization suggestion..." : "Keeping this resource as it is...", "progress");
  try {
    await workspaceRequest(`/api/v1/proposals/${item.proposalId}/${decision}`, { method: "POST" });
    await refreshCatalogSnapshot({ preserveViewport: true });
    await refreshWorkspaceSession();
    renderPreservingViewport();
    setActionFeedback(decision === "accept" ? "Organization applied. This item has its own undo record." : "Suggestion declined. The resource stayed in the library unchanged.", "success");
  } catch (error) {
    showWorkspaceError(error, "The organization decision could not be saved.");
  } finally {
    if (button?.isConnected) button.disabled = false;
  }
}

async function requestOrganizationBatchDecision(batch, decision, button = null) {
  const count = Number(batch?.counts?.proposed || 0);
  if (!workspace.interactive || !batch || !count) return;
  const verb = decision === "accept" ? "apply" : "decline";
  const question = decision === "accept"
    ? `Apply ${formatNumber(count)} current organization suggestions? This changes TabAtlas only; browser tabs are untouched. Every applied item gets its own undo record.`
    : `Decline ${formatNumber(count)} current organization suggestions? Saved resources remain in the library unchanged.`;
  if (!window.confirm(question)) return;
  if (button) button.disabled = true;
  setActionFeedback(`${verb === "apply" ? "Applying" : "Declining"} ${formatNumber(count)} suggestions...`, "progress");
  try {
    const result = await workspaceRequest(`/api/v1/organization-batches/${batch.id}/${decision}`, { method: "POST" });
    await refreshCatalogSnapshot({ preserveViewport: true });
    await refreshWorkspaceSession();
    renderPreservingViewport();
    const skipped = Number(result.skipped || 0);
    setActionFeedback(`${formatNumber(result.decided || 0)} suggestions ${decision === "accept" ? "applied" : "declined"}.${skipped ? ` ${formatNumber(skipped)} changed items were skipped safely.` : ""}`, skipped ? "warning" : "success");
  } catch (error) {
    showWorkspaceError(error, "The organization batch decision could not be saved.");
  } finally {
    if (button?.isConnected) button.disabled = false;
  }
}

async function requestOrganizationBatchDefer(batch, button = null) {
  const count = Number(batch?.counts?.needsContext || 0);
  if (!workspace.interactive || !batch || !count) return;
  if (!window.confirm(`Leave ${formatNumber(count)} resources unorganized? They stay saved and searchable, but they will no longer require your attention.`)) return;
  if (button) button.disabled = true;
  setActionFeedback(`Leaving ${formatNumber(count)} resources unorganized...`, "progress");
  try {
    const result = await workspaceRequest(`/api/v1/organization-batches/${batch.id}/defer`, { method: "POST" });
    await refreshCatalogSnapshot({ preserveViewport: true });
    await refreshWorkspaceSession();
    renderPreservingViewport();
    setActionFeedback(`${formatNumber(result.decided || 0)} resources left unorganized. They remain saved and searchable.`, "success");
  } catch (error) {
    showWorkspaceError(error, "The remaining resources could not be deferred.");
  } finally {
    if (button?.isConnected) button.disabled = false;
  }
}
