function requestCapturedTabArchive() {
  const currentTabs = currentTabCount();
  const dismissedOpenResources = inventoryCount("currentDismissedResources", 0);
  if (!currentTabs || pendingDiscoveryCount()) return;
  downloadActionRequest("archive_captured_tabs", {
    libraryResources: libraryResourceCount(),
    currentResources: currentResourceCount(),
    currentTabs,
    dismissedOpenResources
  });
}

async function requestDiscoveryAcceptance(resource = null) {
  const selected = resource
    ? [resource.resourceId]
    : discoveries.map(item => item.resourceId);
  await requestDiscoveryDecision("accept", selected);
}

async function requestDiscoveryDismissal(resource) {
  if (!resource) return;
  await requestDiscoveryDecision("dismiss", [resource.resourceId]);
}

async function requestDiscoveryDecision(decision, selected) {
  const resourceIds = unique(selected.filter(resourceId => resourceById.has(resourceId)));
  if (!resourceIds.length) return;
  if (!workspace.interactive) {
    downloadActionRequest(
      decision === "accept" ? "accept_discoveries" : "dismiss_discoveries",
      {
        pendingDiscoveries: pendingDiscoveryCount(),
        selectedResources: resourceIds.length
      },
      resourceIds
    );
    return;
  }
  const buttons = [...document.querySelectorAll(".accept-resource-command, .dismiss-resource-command")];
  buttons.forEach(button => { button.disabled = true; });
  try {
    const result = await workspaceRequest(`/api/v1/discoveries/${decision}`, {
      method: "POST",
      json: { resourceIds }
    });
    const restored = decision === "accept" && resourceIds.some(resourceId => resourceById.get(resourceId)?.libraryState === "dismissed");
    elements.actionStatus.textContent = decision === "dismiss"
      ? `${formatNumber(result.updated)} resource${result.updated === 1 ? "" : "s"} dismissed. Browser tabs were unchanged.`
      : `${formatNumber(result.updated)} resource${result.updated === 1 ? "" : "s"} ${restored ? "restored" : "added"} to the library.`;
    await refreshCatalogSnapshot().catch(error => {
      showWorkspaceError(error, "The decision was saved, but the updated catalog could not be loaded.");
    });
  } catch (error) {
    showWorkspaceError(error, "The discovery decision could not be saved.");
  } finally {
    buttons.forEach(button => { button.disabled = false; });
  }
}

function requestDuplicateClose(resource = null) {
  const safeDuplicateCandidates = resource
    ? duplicateSummary(resource).safeCloseCandidates
    : safeDuplicateCount();
  if (!safeDuplicateCandidates) return;
  const matchingResources = resource
    ? 1
    : exactDuplicateResources().filter(item => duplicateSummary(item).safeCloseCandidates > 0).length;
  downloadActionRequest("close_exact_duplicates", {
    libraryResources: libraryResourceCount(),
    currentResources: currentResourceCount(),
    currentTabs: currentTabCount(),
    matchingResources,
    safeDuplicateCandidates
  }, resource ? [resource.resourceId] : []);
}

async function requestLibraryRemoval(resource) {
  if (!resource || resource.libraryState !== "accepted") return;
  if (workspace.interactive) {
    if (!window.confirm("Remove this resource from the visible library? It remains recoverable and no browser tab will be changed.")) return;
    try {
      await workspaceRequest(`/api/v1/resources/${resource.resourceId}/remove`, { method: "POST" });
      elements.actionStatus.textContent = "Resource removed from the visible library.";
      await refreshCatalogSnapshot().catch(error => {
        showWorkspaceError(error, "The resource was removed, but the updated catalog could not be loaded.");
      });
    } catch (error) {
      showWorkspaceError(error, "The resource could not be removed.");
    }
    return;
  }
  downloadActionRequest("remove_from_library", {
    libraryResources: libraryResourceCount(),
    selectedResources: 1
  }, [resource.resourceId]);
}

function requestResourcePreview(resource) {
  if (!resource) return;
  downloadActionRequest("capture_resource_preview", {
    selectedResources: 1,
    existingImagePreviews: resource.presentation?.preview?.localImage ? 1 : 0
  }, [resource.resourceId]);
}

function requestResourceReclassification(resource) {
  if (!resource) return;
  if (workspace.interactive) {
    state.selectedResource = resource.resourceId;
    renderDrawer();
    setAgentPanel(true);
    elements.agentInput.placeholder = "Describe what this resource means to you";
    elements.actionStatus.textContent = "Codex is scoped to this resource.";
    return;
  }
  downloadActionRequest("reconsider_resource", {
    selectedResources: 1,
    libraryResources: libraryResourceCount()
  }, [resource.resourceId]);
}

function resourceSourceUrl(resource) {
  const value = String(resource?.openUrl || resource?.canonicalUrl || "");
  return /^(https?|file):/i.test(value) ? value : "";
}

async function copyResourceLink(resource) {
  const value = resourceSourceUrl(resource);
  if (!value) return;
  let copied = false;
  try {
    await navigator.clipboard.writeText(value);
    copied = true;
  } catch {
    const input = document.createElement("textarea");
    input.value = value;
    input.setAttribute("readonly", "");
    input.style.position = "fixed";
    input.style.opacity = "0";
    document.body.append(input);
    input.select();
    copied = document.execCommand("copy");
    input.remove();
  }
  elements.actionStatus.textContent = copied ? "Source link copied." : "The source link could not be copied.";
}

function downloadActionRequest(action, counts, resourceIds = []) {
  const payload = {
    schemaVersion: 1,
    action,
    generatedAt: new Date().toISOString()
  };
  const safeResourceIds = unique(resourceIds.filter(resourceId => resourceById.has(resourceId)));
  if (safeResourceIds.length) payload.resourceIds = safeResourceIds;
  payload.counts = Object.fromEntries(Object.entries(counts).map(([key, value]) => [key, Math.max(0, Number(value) || 0)]));
  const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = "tabatlas-action-request.json";
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
  elements.actionStatus.textContent = "Action request downloaded. No browser tabs were changed.";
}
