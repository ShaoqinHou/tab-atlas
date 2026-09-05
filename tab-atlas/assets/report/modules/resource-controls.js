// Resource chips, commands, and card footer actions.
function resourceChips(resource, hideSpace = false) {
  const chips = node("div", "resource-chips");
  const space = resourceSpace(resource);
  const topics = resourceTopics(resource);
  const focuses = resourceFocuses(resource);
  const projects = resourceProjects(resource);
  const actionLists = resourceActionLists(resource);
  const duplicate = duplicateSummary(resource);
  if (!hideSpace && space) chips.append(chip(space, "space-chip"));
  if (topics[0]) chips.append(chip(topics[0], "topic-chip"));
  if (focuses[0]) chips.append(chip(focuses[0], "focus-chip"));
  else if (projects[0]) chips.append(chip(projects[0], "project-chip"));
  if (actionLists[0]) chips.append(chip(actionLists[0], "action-chip"));
  if (Number(resource.noteCount || 0)) chips.append(chip("Your note", "note-chip"));
  if (duplicate.sets) {
    const count = duplicate.safeCloseCandidates || duplicate.instances;
    const qualifier = duplicate.safeCloseCandidates ? "" : " protected";
    chips.append(chip(`${count}${qualifier} exact ${count === 1 ? "copy" : "copies"}`, "duplicate-chip"));
  }
  return chips;
}

function resourceCommands(resource, placement) {
  const wrapper = node("div", `resource-commands ${placement}-resource-commands`);
  const sourceUrl = resourceSourceUrl(resource);
  if (sourceUrl) {
    const open = node("a", "resource-command-link", "Open source");
    open.href = sourceUrl;
    open.target = "_blank";
    open.rel = "noreferrer";
    wrapper.append(open);

    const copy = node("button", "resource-command-button", "Copy link");
    copy.type = "button";
    copy.addEventListener("click", () => copyResourceLink(resource));
    wrapper.append(copy);
  }

  const menu = document.createElement("details");
  menu.className = "resource-command-menu";
  const summary = node("summary", "", "More");
  summary.title = `More actions for ${resourceDisplayTitle(resource)}`;
  menu.append(summary);
  const choices = node("div", "resource-command-choices");
  const preview = resource.presentation?.preview || {};
  if (!preview.localImage && preview.canRequestRicher !== false) {
    const requestLabel = preview.requestLabel || "richer preview";
    choices.append(menuCommand(`Request ${requestLabel}`, () => requestResourcePreview(resource)));
  }
  choices.append(menuCommand("Reclassify", () => requestResourceReclassification(resource)));
  if (resource.libraryState === "accepted") {
    choices.append(menuCommand("Remove from library", () => requestLibraryRemoval(resource), "danger"));
  }
  menu.append(choices);
  wrapper.append(menu);
  return wrapper;
}

function menuCommand(label, onClick, tone = "") {
  const button = node("button", tone, label);
  button.type = "button";
  button.addEventListener("click", event => {
    onClick();
    const menu = event.currentTarget.closest("details");
    if (menu) menu.open = false;
  });
  return button;
}

function resourceFooter(resource, placement) {
  const openCount = liveTabs(resource).length;
  const duplicateCount = duplicateSummary(resource).safeCloseCandidates;
  const wrapper = node("div", `resource-footer ${placement}-resource-footer`);
  if (resource.libraryState === "candidate") {
    wrapper.classList.add("has-command", "is-discovery");
    const copy = node("span", "resource-footer-copy");
    copy.append(
      node("strong", "", "New discovery"),
      node("span", "", "Not yet in the durable library")
    );
    const actions = node("span", "resource-footer-actions");
    const accept = node("button", "resource-action-command accept-resource-command", "Add");
    accept.type = "button";
    accept.addEventListener("click", () => requestDiscoveryAcceptance(resource));
    const openTabs = liveTabs(resource).length;
    if (openTabs) {
      const acceptAndClose = node(
        "button",
        "resource-action-command accept-close-resource-command",
        openTabs === 1 ? "Add + close tab" : `Add + close ${formatNumber(openTabs)} tabs`
      );
      acceptAndClose.type = "button";
      acceptAndClose.title = "Save this page first, then close only its freshly captured browser tab after exact revalidation.";
      acceptAndClose.addEventListener("click", () => requestDiscoveryAcceptance(resource, true));
      actions.append(acceptAndClose);
    }
    const dismiss = node("button", "resource-action-command dismiss-resource-command", "Dismiss");
    dismiss.type = "button";
    dismiss.addEventListener("click", () => requestDiscoveryDismissal(resource));
    actions.prepend(accept);
    actions.append(dismiss);
    wrapper.append(copy, actions);
    return wrapper;
  }
  if (resource.libraryState === "dismissed") {
    wrapper.classList.add("has-command", "is-dismissed");
    const copy = node("span", "resource-footer-copy");
    copy.append(
      node("strong", "", "Dismissed"),
      node("span", "", "Outside the library; retained for recovery")
    );
    const restore = node("button", "resource-action-command", "Restore to library");
    restore.type = "button";
    restore.addEventListener("click", () => requestDiscoveryAcceptance(resource));
    wrapper.append(copy, restore);
    return wrapper;
  }
  const organizationReview = state.view === "review" && ["inbox", "organization"].includes(state.reviewMode);
  if (duplicateCount && !organizationReview) {
    wrapper.classList.add("has-command");
    const copy = node("span", "resource-footer-copy");
    copy.append(
      node("strong", "", `${formatNumber(duplicateCount)} safe ${duplicateCount === 1 ? "extra" : "extras"}`),
      node("span", "", "Request only; verified first")
    );
    const button = node("button", "resource-action-command", "Close duplicate extras");
    button.type = "button";
    button.title = "Downloads a privacy-safe action request; it does not close tabs directly.";
    button.setAttribute("aria-label", `Close duplicate extras for ${resourceDisplayTitle(resource)}. ${REQUEST_SAFETY_TEXT}`);
    button.addEventListener("click", () => requestDuplicateClose(resource));
    wrapper.append(copy, button);
    return wrapper;
  }

  const isSavedUnorganized = resource.libraryState === "accepted"
    && !resourceSpace(resource)
    && state.view === "review"
    && state.reviewMode === "inbox";
  if (isSavedUnorganized) {
    wrapper.classList.add("has-command", "is-stored", "is-unorganized");
    const item = organizationBatchItem(resource, "unorganized");
    const copy = node("span", "resource-footer-copy");
    if (item?.effectiveState === "proposed") {
      copy.append(
        node("strong", "", "Analyzed — suggestion ready"),
        node("span", "", item.proposedPath?.join(" → ") || "Purpose path proposed")
      );
      const actions = node("span", "resource-footer-actions");
      const apply = node("button", "resource-action-command organize-resource-command", "Apply");
      apply.type = "button";
      apply.addEventListener("click", () => requestOrganizationProposalDecision(item, "accept", apply));
      const keep = node("button", "resource-action-command", "Keep as-is");
      keep.type = "button";
      keep.addEventListener("click", () => requestOrganizationProposalDecision(item, "reject", keep));
      actions.append(apply, keep);
      wrapper.append(copy, actions);
    } else if (item?.effectiveState === "needsContext") {
      copy.append(node("strong", "", "Analyzed — needs your context"), node("span", "", item.rationale || "Codex could not infer a safe purpose path"));
      const context = node("button", "resource-action-command organize-resource-command", "Add context");
      context.type = "button";
      context.addEventListener("click", () => requestResourceReclassification(resource));
      wrapper.append(copy, context);
    } else if (item?.effectiveState === "rejected") {
      copy.append(node("strong", "", "Suggestion declined"), node("span", "", "Saved and searchable as-is"));
      wrapper.append(copy);
    } else if (item?.effectiveState === "unchanged") {
      copy.append(node("strong", "", "Left unorganized"), node("span", "", "Saved, searchable, and no longer waiting for attention"));
      wrapper.append(copy);
    } else if (item) {
      copy.append(node("strong", "", "Analyzed — no change suggested"), node("span", "", "Saved and searchable as-is"));
      wrapper.append(copy);
    } else {
      copy.append(node("strong", "", "Saved — not analyzed in this batch"), node("span", "", "Still in the library and searchable"));
      const organize = node("button", "resource-action-command organize-resource-command", "Add context");
      organize.type = "button";
      organize.title = "Tell Codex what this saved resource means to you.";
      organize.addEventListener("click", () => requestResourceReclassification(resource));
      wrapper.append(copy, organize);
    }
    return wrapper;
  }

  if (state.view === "review" && state.reviewMode === "organization") {
    const item = organizationBatchItem(resource);
    if (item?.effectiveState === "proposed") {
      wrapper.classList.add("has-command", "is-stored", "is-unorganized");
      const copy = node("span", "resource-footer-copy");
      copy.append(node("strong", "", "Organization suggested"), node("span", "", item.proposedPath?.join(" → ") || "Purpose path proposed"));
      const actions = node("span", "resource-footer-actions");
      const apply = node("button", "resource-action-command organize-resource-command", "Apply");
      apply.type = "button";
      apply.addEventListener("click", () => requestOrganizationProposalDecision(item, "accept", apply));
      const keep = node("button", "resource-action-command", "Keep as-is");
      keep.type = "button";
      keep.addEventListener("click", () => requestOrganizationProposalDecision(item, "reject", keep));
      actions.append(apply, keep);
      wrapper.append(copy, actions);
      return wrapper;
    }
    if (item?.effectiveState === "needsContext") {
      wrapper.classList.add("has-command", "is-stored", "is-unorganized");
      const copy = node("span", "resource-footer-copy");
      copy.append(node("strong", "", "Analyzed — needs your context"), node("span", "", item.rationale || "A safe purpose could not be inferred"));
      const context = node("button", "resource-action-command organize-resource-command", "Add context");
      context.type = "button";
      context.addEventListener("click", () => requestResourceReclassification(resource));
      wrapper.append(copy, context);
      return wrapper;
    }
    if (item?.effectiveState === "unchanged") {
      wrapper.classList.add("is-stored");
      const copy = node("span", "resource-footer-copy");
      copy.append(
        node("strong", "", latestOrganizationBatch()?.scope === "unorganized" ? "Left unorganized" : "Analyzed — no change suggested"),
        node("span", "", item.rationale || "Saved and searchable as-is")
      );
      wrapper.append(copy);
      return wrapper;
    }
    if (["accepted", "rejected", "superseded"].includes(item?.effectiveState)) {
      wrapper.classList.add("is-stored");
      const copy = node("span", "resource-footer-copy");
      const applied = ["accepted", "superseded"].includes(item.effectiveState);
      copy.append(
        node("strong", "", applied ? "Suggestion applied" : "Suggestion declined"),
        node("span", "", item.effectiveState === "superseded" ? "Organized by another approved batch" : (applied ? "Recorded with an individual undo trail" : "Resource kept as-is"))
      );
      wrapper.append(copy);
      return wrapper;
    }
  }

  wrapper.classList.add(openCount ? "is-open" : "is-stored");
  const copy = node("span", "resource-footer-copy");
  copy.append(
    node("strong", "", openCount ? `${formatNumber(openCount)} open ${openCount === 1 ? "tab" : "tabs"}` : "Stored"),
    node("span", "", openCount ? "Captured in library" : "No open browser tabs")
  );
  wrapper.append(copy);
  return wrapper;
}
