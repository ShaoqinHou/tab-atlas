function setAgentPanel(open) {
  if (!workspace.interactive) return;
  state.agentOpen = Boolean(open);
  renderAgentPanel();
  if (state.agentOpen) requestAnimationFrame(() => elements.agentInput.focus({ preventScroll: true }));
}

function renderAgentPanel() {
  elements.agentPanel.hidden = !state.agentOpen || !workspace.interactive;
  elements.agentToggle.setAttribute("aria-expanded", String(state.agentOpen));
  if (!workspace.interactive) return;
  const pendingCount = workspace.messages.filter(message => message.proposalId && !message.decision).length;
  elements.agentPending.textContent = String(pendingCount);
  elements.agentPending.hidden = pendingCount === 0;
  const selected = state.selectedResource ? resourceById.get(state.selectedResource) : null;
  elements.agentScope.textContent = selected ? resourceDisplayTitle(selected) : "Whole library";
  elements.agentInput.placeholder = selected
    ? "Describe what this resource means to you"
    : "Ask about this resource library";
  const ownership = workspace.agent.ownership || "workspace";
  elements.agentStatus.textContent = ownership === "codex_desktop"
    ? "Open in Codex desktop"
    : (workspace.agent.running ? workspace.agent.model || "Connected" : "Starts when asked");
  elements.agentOwnership.textContent = ownership === "codex_desktop" ? "Reclaim here" : "Open in Codex";
  elements.agentMessages.replaceChildren();
  if (!workspace.messages.length) {
    elements.agentMessages.append(node("p", "agent-empty", selected ? "Ask about the selected resource." : "Ask across the retained library."));
  }
  for (const message of workspace.messages) {
    const item = node("div", `agent-message ${message.role}`);
    item.append(node("p", "", message.text));
    if (message.proposalId && message.proposal) item.append(agentProposalSummary(message.proposal));
    const actions = node("div", "agent-message-actions");
    if (message.proposalId && !message.decision) {
      const apply = node("button", "primary-command", "Accept changes");
      apply.type = "button";
      apply.addEventListener("click", () => decideAgentProposal(message, true, apply));
      const dismiss = node("button", "text-command", "Dismiss");
      dismiss.type = "button";
      dismiss.addEventListener("click", () => decideAgentProposal(message, false, dismiss));
      const discuss = node("button", "secondary-command", "Discuss");
      discuss.type = "button";
      discuss.addEventListener("click", () => discussAgentProposal(message));
      actions.append(apply, discuss, dismiss);
    } else if (message.decision) {
      const label = message.decision === "accepted"
        ? "Accepted"
        : (message.decision === "rejected" ? "Dismissed" : "Superseded");
      actions.append(node("span", "proposal-decision", label));
    }
    if (message.auditId) {
      const undo = node("button", "text-command", "Undo");
      undo.type = "button";
      undo.addEventListener("click", () => undoSemanticChange(message.resourceId, message.auditId, undo));
      actions.append(undo);
    }
    if (actions.childElementCount) item.append(actions);
    elements.agentMessages.append(item);
  }
  if (workspace.previousView) {
    const back = node("button", "agent-back", "Back before Codex navigation");
    back.type = "button";
    back.addEventListener("click", restoreAgentNavigation);
    elements.agentMessages.append(back);
  }
  elements.agentMessages.scrollTop = elements.agentMessages.scrollHeight;
}

function agentProposalSummary(proposal) {
  const summary = node("div", "agent-proposal");
  const memberships = Array.isArray(proposal.memberships) ? proposal.memberships : [];
  const primary = ["space", "topic", "focus"]
    .map(kind => memberships.find(item => item.kind === kind)?.name)
    .filter(Boolean);
  if (primary.length) summary.append(proposalLine("Purpose", primary.join(" / ")));
  const projects = memberships.filter(item => item.kind === "project").map(item => item.name);
  if (projects.length) summary.append(proposalLine("Projects", projects.join(", ")));
  const actionLists = memberships.filter(item => item.kind === "action_list").map(item => item.name);
  if (actionLists.length) summary.append(proposalLine("Action Lists", actionLists.join(", ")));
  if (proposal.actionItem?.listName) {
    const effort = proposal.actionItem.estimatedMinutes ? `, ${formatNumber(proposal.actionItem.estimatedMinutes)} min` : "";
    summary.append(proposalLine("Next", `${proposal.actionItem.listName}, priority ${proposal.actionItem.priority || 3}${effort}`));
  }
  if (!summary.childElementCount) summary.append(proposalLine("Suggestion", "No organization change"));
  return summary;
}

function proposalLine(label, value) {
  const line = node("div", "agent-proposal-line");
  line.append(node("strong", "", label), node("span", "", value));
  return line;
}

function discussAgentProposal(message) {
  if (message.resourceId && resourceById.has(message.resourceId)) state.selectedResource = message.resourceId;
  elements.agentInput.value = "Refine this pending suggestion. Explain the tradeoffs and propose a better organization if needed.";
  renderAgentPanel();
  requestAnimationFrame(() => {
    elements.agentInput.focus({ preventScroll: true });
    elements.agentInput.setSelectionRange(elements.agentInput.value.length, elements.agentInput.value.length);
  });
}

async function submitAgentRequest(event) {
  event.preventDefault();
  const message = elements.agentInput.value;
  if (!message.trim()) return;
  const send = elements.agentForm.querySelector('button[type="submit"]');
  send.disabled = true;
  const resourceId = state.selectedResource || "";
  workspace.messages.push({ role: "user", text: message.trim() });
  elements.agentInput.value = "";
  renderAgentPanel();
  try {
    const request = await workspaceRequest("/api/v1/agent-requests", {
      method: "POST",
      json: { message, resourceId }
    });
    workspace.messages.push({ role: "status", text: "Codex is considering the current scope." });
    renderAgentPanel();
    pollAgentRequest(request.id, resourceId);
  } catch (error) {
    showWorkspaceError(error, "Codex request could not be queued.");
  } finally {
    send.disabled = false;
  }
}

function pollAgentRequest(requestId, resourceId = "") {
  if (!requestId || workspace.requestPolls.has(requestId)) return;
  let deferredChecks = 0;
  const poll = async () => {
    try {
      const request = await workspaceRequest(`/api/v1/agent-requests/${requestId}`);
      if (request.status === "queued" && request.errorCode) {
        deferredChecks += 1;
        if (deferredChecks >= 6) {
          workspace.messages.push({ role: "status", text: "Saved for the next available Codex session." });
          workspace.requestPolls.delete(requestId);
          renderAgentPanel();
          return;
        }
      }
      if (request.status === "failed") {
        workspace.messages = workspace.messages.filter(message => message.role !== "status");
        workspace.messages.push({ role: "assistant", text: "The note remains saved, but Codex could not complete this review in the current session." });
        workspace.requestPolls.delete(requestId);
        renderAgentPanel();
        return;
      }
      if (request.status !== "completed") {
        const timer = window.setTimeout(poll, request.errorCode ? 2500 : 1200);
        workspace.requestPolls.set(requestId, timer);
        return;
      }
      workspace.requestPolls.delete(requestId);
      workspace.messages = workspace.messages.filter(message => message.role !== "status");
      const response = request.response || {};
      workspace.messages.push({
        role: "assistant",
        text: response.message || response.interpretation || "Codex completed the request.",
        proposal: response,
        proposalId: request.proposalId,
        decision: request.decision || (request.proposalId && !request.proposalCurrent ? "superseded" : ""),
        auditId: request.auditId,
        resourceId: request.resourceId || resourceId
      });
      if (request.auditId && request.resourceId) workspace.latestAuditByResource.set(request.resourceId, request.auditId);
      if (request.resourceId) await loadResourceWorkspaceState(request.resourceId, true);
      await refreshWorkspaceSession();
      applyAgentNavigation(response.navigation);
      render();
    } catch (error) {
      workspace.requestPolls.delete(requestId);
      showWorkspaceError(error, "Codex status could not be read.");
    }
  };
  const timer = window.setTimeout(poll, 500);
  workspace.requestPolls.set(requestId, timer);
}

async function decideAgentProposal(message, accept, button) {
  button.disabled = true;
  try {
    const action = accept ? "accept" : "reject";
    const result = await workspaceRequest(`/api/v1/proposals/${message.proposalId}/${action}`, { method: "POST" });
    for (const candidate of workspace.messages) {
      if (candidate.proposalId === message.proposalId) {
        candidate.decision = result.decision;
        candidate.auditId = result.auditId;
      } else if (accept && result.resourceId && candidate.resourceId === result.resourceId && !candidate.decision) {
        candidate.decision = "superseded";
      }
    }
    if (result.auditId && result.resourceId) workspace.latestAuditByResource.set(result.resourceId, result.auditId);
    if (result.resourceId) await loadResourceWorkspaceState(result.resourceId, true);
    await refreshWorkspaceSession();
    elements.actionStatus.textContent = accept ? "Codex suggestion accepted and saved." : "Codex suggestion dismissed. No organization changed.";
    render();
  } catch (error) {
    showWorkspaceError(error, "The proposal decision could not be saved.");
  } finally {
    button.disabled = false;
  }
}

function applyAgentNavigation(navigation) {
  if (!navigation || navigation.command === "none") return;
  if (navigation.command === "browser_sync") {
    const browsers = Array.isArray(navigation.browsers) && navigation.browsers.length
      ? navigation.browsers
      : ["chrome", "edge"];
    startBrowserSync(browsers);
    elements.actionStatus.textContent = "Codex started a browser sync.";
    return;
  }
  workspace.previousView = {
    view: state.view,
    search: state.search,
    scope: state.scope ? { ...state.scope } : null,
    sourceScope: state.sourceScope,
    sourcePublisher: state.sourcePublisher,
    sourceTopic: state.sourceTopic,
    reviewMode: state.reviewMode,
    filters: { ...state.filters },
    selectedResource: state.selectedResource
  };
  if (navigation.command === "open_resource" && resourceById.has(navigation.resourceId)) {
    state.selectedResource = navigation.resourceId;
    render();
    highlightResource(navigation.resourceId);
  } else if (navigation.command === "show_collection" && navigation.collectionName) {
    const summary = [...spaceSummaries, ...projectSummaries, ...actionListSummaries]
      .find(item => item.name === navigation.collectionName);
    if (summary) {
      const kind = actionListSummaries.includes(summary) ? "action_list" : (projectSummaries.includes(summary) ? "project" : "space");
      openScope(kind, summary.id);
    }
  } else if (navigation.command === "set_filter" && navigation.filter) {
    state.search = navigation.filter.toLocaleLowerCase();
    elements.search.value = navigation.filter;
    renderAtTop();
  }
  elements.actionStatus.textContent = "Codex changed the current view.";
}

function restoreAgentNavigation() {
  const previous = workspace.previousView;
  if (!previous) return;
  workspace.previousView = null;
  Object.assign(state, previous);
  elements.search.value = previous.search;
  renderAtTop();
}

function highlightResource(resourceId) {
  requestAnimationFrame(() => {
    const card = document.querySelector(`[data-resource-id="${CSS.escape(resourceId)}"]`);
    if (!card) return;
    card.classList.add("agent-highlight");
    card.scrollIntoView({ block: "center", behavior: "smooth" });
    window.setTimeout(() => card.classList.remove("agent-highlight"), 2400);
  });
}

async function toggleAgentOwnership() {
  elements.agentOwnership.disabled = true;
  try {
    if (workspace.agent.ownership === "codex_desktop") {
      const status = await workspaceRequest("/api/v1/agent/reclaim", { method: "POST" });
      workspace.agent = status;
      workspace.agent.ownership = "workspace";
    } else {
      const handoff = await workspaceRequest("/api/v1/agent/handoff", { method: "POST" });
      workspace.agent.ownership = handoff.ownership;
      renderAgentPanel();
      if (handoff.deepLink) window.location.href = handoff.deepLink;
    }
    renderAgentPanel();
  } catch (error) {
    showWorkspaceError(error, "Codex ownership could not be changed.");
  } finally {
    elements.agentOwnership.disabled = false;
  }
}
