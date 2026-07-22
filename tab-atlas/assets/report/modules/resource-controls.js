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
    const accept = node("button", "resource-action-command accept-resource-command", "Add to library");
    accept.type = "button";
    accept.addEventListener("click", () => requestDiscoveryAcceptance(resource));
    const dismiss = node("button", "resource-action-command dismiss-resource-command", "Dismiss");
    dismiss.type = "button";
    dismiss.addEventListener("click", () => requestDiscoveryDismissal(resource));
    actions.append(accept, dismiss);
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
  if (duplicateCount) {
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

  wrapper.classList.add(openCount ? "is-open" : "is-stored");
  const copy = node("span", "resource-footer-copy");
  copy.append(
    node("strong", "", openCount ? `${formatNumber(openCount)} open ${openCount === 1 ? "tab" : "tabs"}` : "Stored"),
    node("span", "", openCount ? "Captured in library" : "No open browser tabs")
  );
  wrapper.append(copy);
  return wrapper;
}
