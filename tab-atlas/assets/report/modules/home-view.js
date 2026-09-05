// Home dashboard renderer.
function renderHome() {
  const fragment = document.createDocumentFragment();
  const inbox = inboxResources();
  const safeDuplicates = safeDuplicateCount();
  const openTabs = currentTabCount();
  const pendingDiscoveries = pendingDiscoveryCount();
  const liveResources = currentResourceCount();
  const libraryResources = libraryResourceCount();
  const purposeSpaces = spaceSummaries.slice(0, 6);

  const intro = node("section", "home-head");
  const introCopy = node("div", "home-copy");
  introCopy.append(
    node("p", "eyebrow", "Durable library"),
    node("h2", "", "Your saved library is separate from your open tabs"),
    node("p", "scope-summary", openTabs
      ? `${formatNumber(libraryResources)} library resources; ${formatNumber(liveResources)} are represented by ${formatNumber(openTabs)} open tabs.`
      : `${formatNumber(libraryResources)} library resources. No captured tabs are currently open.`)
  );
  intro.append(introCopy, continueButton(pendingDiscoveries, inbox.length, safeDuplicates, openTabs));
  fragment.append(intro);

  const attention = node("section", "attention-section");
  attention.append(sectionHeading("Library status", "Only new discoveries need a decision. Organization is optional after an item is saved."));
  const attentionRow = node("div", "attention-row");
  attentionRow.append(
    attentionButton("Needs approval", pendingDiscoveries, "Not in the library yet", () => openReview("discoveries")),
    attentionButton("Saved, not organized", inbox.length, unorganizedAnalysisStatus(), () => openReview("inbox")),
    attentionButton("Safe exact duplicates", safeDuplicates, "Closeable extras", () => openReview("duplicates")),
    attentionButton("Open tabs", openTabs, `${formatNumber(liveResources)} live resources`, () => openReview("open"))
  );
  attention.append(attentionRow);
  fragment.append(attention);

  if (actionListSummaries.length) {
    const actionLists = node("section", "section-block action-lists-section");
    actionLists.append(sectionHeading("Action lists", "Resources with an explicit follow-through state."));
    const list = node("div", "action-list-directory");
    for (const summary of actionListSummaries.slice(0, 6)) list.append(actionListRow(summary));
    actionLists.append(list);
    fragment.append(actionLists);
  }

  const spacesSection = node("section", "section-block");
  spacesSection.append(sectionHeading(
    "Purpose spaces",
    "Resources organized by how they support the user's work.",
    spaceSummaries.length > 6 ? "All spaces" : "",
    spaceSummaries.length > 6 ? () => setView("spaces") : null
  ));
  if (purposeSpaces.length) {
    const grid = node("div", "space-grid");
    for (const summary of purposeSpaces) grid.append(spaceCard(summary));
    spacesSection.append(grid);
  } else {
    spacesSection.append(emptyState("No purpose spaces have been assigned yet."));
  }
  fragment.append(spacesSection);

  const workspaces = node("section", "section-block active-workspaces");
  workspaces.append(sectionHeading("Active workspaces", "Project-specific material kept separate from broad interests."));
  if (projectSummaries.length) {
    const list = node("div", "workspace-list");
    for (const summary of projectSummaries) list.append(workspaceRow(summary));
    workspaces.append(list);
  } else {
    workspaces.append(node("p", "quiet-empty", "No active project workspaces in this capture."));
  }
  fragment.append(workspaces);
  elements.screen.replaceChildren(fragment);
}

function continueButton(discoveryCount, inboxCount, duplicateCount, openTabCount) {
  const button = node("button", "continue-action");
  button.type = "button";
  let count = openTabCount;
  let label = "Review open tabs";
  let detail = duplicateCount
    ? `${formatNumber(duplicateCount)} safe exact extras can be removed first, or request the full archive.`
    : "Captured resources remain in the library after their browser tabs close.";
  let onClick = () => openReview("open");
  if (discoveryCount) {
    count = discoveryCount;
    label = "Decide on new discoveries";
    detail = "Add individual resources, dismiss them, or accept the complete batch.";
    onClick = () => openReview("discoveries");
  } else if (!openTabCount) {
    count = libraryResourceCount();
    label = "Browse your saved library";
    detail = inboxCount
      ? `${formatNumber(inboxCount)} saved resources are not organized yet. That is optional; nothing is waiting for approval.`
      : "Browse the retained library by the work each resource supports.";
    onClick = () => setView("spaces");
  }
  button.append(
    node("span", "continue-kicker", "Continue"),
    node("strong", "", label),
    node("span", "continue-detail", detail),
    node("span", "continue-count", formatNumber(count))
  );
  button.addEventListener("click", onClick);
  return button;
}

function attentionButton(label, count, detail, onClick) {
  const button = node("button", "attention-button");
  button.type = "button";
  button.append(node("strong", "", formatNumber(count)), node("span", "", label), node("small", "", detail));
  button.addEventListener("click", onClick);
  return button;
}
