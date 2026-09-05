// Discovery, inbox, duplicate, and open-tab review renderer.
function renderReview() {
  const openResources = openTabResources();
  const modes = [
    ["discoveries", "Needs approval", discoveries.length],
    ["inbox", "Saved, not organized", inboxResources().length],
    ["organization", "Organization", organizationAttentionCount()],
    ["open", "Open tabs", currentTabCount()],
    ["duplicates", "Exact duplicates", exactDuplicateResources().length],
    ["dismissed", "Dismissed", dismissed.length]
  ];
  const page = node("section", "review-page");
  const head = node("header", "review-head");
  const copy = node("div");
  copy.append(
    node("p", "eyebrow", "Manage library"),
    node("h2", "", modes.find(item => item[0] === state.reviewMode)?.[1] || "Manage"),
    node("p", "scope-summary", reviewDescription(state.reviewMode))
  );
  head.append(copy);
  const requestPanel = reviewActionPanel(state.reviewMode);
  if (requestPanel) head.append(requestPanel);
  page.append(head);

  const control = node("div", "review-modes");
  control.setAttribute("aria-label", "Library management view");
  for (const [id, label, count] of modes) {
    const button = node("button", state.reviewMode === id ? "active" : "");
    button.type = "button";
    button.setAttribute("aria-pressed", String(state.reviewMode === id));
    button.append(node("span", "", label), node("span", "mode-count", formatNumber(count)));
    button.addEventListener("click", () => {
      state.reviewMode = id;
      state.visibleLimit = PAGE_SIZE;
      state.selectedResource = null;
      renderAtTop();
    });
    control.append(button);
  }
  page.append(control);
  const guide = reviewStateGuide(state.reviewMode);
  if (guide) page.append(guide);
  if (state.reviewMode === "organization") page.append(organizationBatchPanel());

  let reviewResources = state.reviewMode === "discoveries" ? discoveries : inboxResources();
  if (state.reviewMode === "organization") reviewResources = organizationReviewResources();
  if (state.reviewMode === "dismissed") reviewResources = dismissed;
  if (state.reviewMode === "duplicates") reviewResources = exactDuplicateResources();
  if (state.reviewMode === "open") reviewResources = openResources;
  let galleryLabel = `${formatNumber(reviewResources.length)} resource${reviewResources.length === 1 ? "" : "s"}`;
  if (state.reviewMode === "discoveries") {
    galleryLabel = `${formatNumber(reviewResources.length)} awaiting approval`;
  } else if (state.reviewMode === "inbox") {
    galleryLabel = `${formatNumber(reviewResources.length)} saved, not organized`;
  } else if (state.reviewMode === "organization") {
    const labels = {
      proposed: ["current suggestion", "current suggestions"],
      needsContext: ["resource needing context", "resources needing context"],
      unchanged: latestOrganizationBatch()?.scope === "unorganized"
        ? ["resource deliberately left unorganized", "resources deliberately left unorganized"]
        : ["resource with no change suggested", "resources with no change suggested"],
      reviewed: ["reviewed organization item", "reviewed organization items"]
    }[state.organizationItemState] || ["organization item", "organization items"];
    galleryLabel = `${formatNumber(reviewResources.length)} ${labels[reviewResources.length === 1 ? 0 : 1]}`;
  } else if (state.reviewMode === "dismissed") {
    galleryLabel = `${formatNumber(reviewResources.length)} dismissed resource${reviewResources.length === 1 ? "" : "s"}`;
  } else if (state.reviewMode === "duplicates") {
    galleryLabel = `${formatNumber(safeDuplicateCount())} safe close candidate${safeDuplicateCount() === 1 ? "" : "s"}`;
  } else if (state.reviewMode === "open") {
    galleryLabel = `${formatNumber(currentTabCount())} open tab${currentTabCount() === 1 ? "" : "s"} across ${formatNumber(openResources.length)} resources`;
  }
  page.append(resourceGallery(reviewResources, {
    label: galleryLabel,
    empty: reviewEmptyMessage(state.reviewMode)
  }));
  elements.screen.replaceChildren(page);
}

function reviewStateGuide(mode) {
  if (!["discoveries", "inbox"].includes(mode)) return null;
  const guide = node("section", `review-state-guide ${mode === "inbox" ? "is-saved" : "needs-decision"}`);
  if (mode === "discoveries") {
    guide.append(
      node("strong", "", "Decision required: not saved to your library yet"),
      node("p", "", "Use Add or Dismiss on each item, or Add all to library for the complete batch. Adding never closes browser tabs by itself.")
    );
  } else {
    const batch = latestOrganizationBatch("unorganized");
    const awaiting = organizationAwaitingAnalysisCount("unorganized");
    const analyzed = Number(batch?.analyzedCount || 0);
    const proposed = Number(batch?.counts?.proposed || 0);
    const context = Number(batch?.counts?.needsContext || 0);
    const deferred = Number(batch?.counts?.unchanged || 0);
    guide.append(
      node("strong", "", batch ? `${formatNumber(analyzed)} resources were analyzed together` : "Saved does not mean analyzed"),
      node("p", "", batch
        ? `${formatNumber(proposed)} have organization suggestions ready; ${formatNumber(context)} need your context; ${formatNumber(deferred)} were deliberately left unorganized; ${formatNumber(awaiting)} newer saved resource${awaiting === 1 ? " has" : "s have"} not been included yet.`
        : "These resources are already in your library, but no whole-cohort organization pass has produced reviewable suggestions yet. They remain searchable and nothing is waiting for library approval.")
    );
  }
  return guide;
}

function reviewActionPanel(mode) {
  if (!["discoveries", "organization", "duplicates", "open"].includes(mode)) return null;
  const duplicateCount = safeDuplicateCount();
  const openTabs = currentTabCount();
  const pending = pendingDiscoveryCount();
  const dismissedOpen = inventoryCount("currentDismissedResources", 0);
  const panel = node("div", "review-action");
  let label = openTabs ? "Close all captured tabs" : "All captured tabs closed";
  let disabled = openTabs === 0;
  if (mode === "discoveries") {
    label = pending ? "Add all to library" : "Nothing needs approval";
    disabled = pending === 0;
  } else if (mode === "organization") {
    const batch = latestOrganizationBatch();
    const showingContext = state.organizationItemState === "needsContext";
    const count = Number(showingContext ? batch?.counts?.needsContext : batch?.counts?.proposed || 0);
    label = showingContext
      ? (count ? `Leave ${formatNumber(count)} unorganized` : "No context decisions remain")
      : (count ? (batch?.strategy?.mode === "best_effort" ? `Apply Codex best judgment to ${formatNumber(count)}` : `Apply ${formatNumber(count)} suggestions`) : "No suggestions ready");
    disabled = count === 0;
  } else if (mode === "duplicates") {
    label = duplicateCount ? "Close all duplicates" : "No safe duplicates";
    disabled = duplicateCount === 0;
  } else if (pending) {
    label = `Review ${formatNumber(pending)} new first`;
    disabled = true;
  } else if (dismissedOpen) {
    label = `Close ${formatNumber(openTabs)} reviewed tabs`;
  }
  const button = node(
    "button",
    `primary-command ${mode === "discoveries" ? "accept-command" : "close-command"}`,
    label
  );
  const note = node(
    "p",
    "action-safety-note",
    mode === "discoveries"
      ? (workspace.interactive
          ? "Adds this reviewed batch to the local library. Browser tabs are unchanged."
          : "Downloads a request to add this reviewed batch. Browser tabs are unchanged.")
      : (mode === "organization"
          ? (state.organizationItemState === "needsContext"
              ? "Clears these from the attention queue without inventing a category. They remain saved, searchable, and available under Left unorganized."
              : "Applies only current suggestions in the selected analysis scope. Context-needed, unchanged, and stale items are left alone. Browser tabs are unchanged.")
          : mode === "open" && dismissedOpen
          ? `${formatNumber(dismissedOpen)} dismissed resource${dismissedOpen === 1 ? "" : "s"} will be discarded; accepted resources remain in the library. ${REQUEST_SAFETY_TEXT}`
          : REQUEST_SAFETY_TEXT)
  );
  note.id = "reviewActionSafety";
  button.type = "button";
  button.disabled = disabled;
  button.title = mode === "discoveries" && workspace.interactive
    ? "Updates the local library only; browser tabs are unchanged."
    : (mode === "organization"
        ? (state.organizationItemState === "needsContext"
            ? "Leaves these resources saved and searchable without assigning a category."
            : "Applies the current organization suggestions to TabAtlas only.")
        : "Downloads a privacy-safe action request; it does not close tabs directly.");
  button.setAttribute("aria-describedby", note.id);
  button.addEventListener("click", () => {
    if (mode === "discoveries") requestDiscoveryAcceptance();
    else if (mode === "organization" && state.organizationItemState === "needsContext") requestOrganizationBatchDefer(latestOrganizationBatch(), button);
    else if (mode === "organization") requestOrganizationBatchDecision(latestOrganizationBatch(), "accept", button);
    else if (mode === "duplicates") requestDuplicateClose();
    else requestCapturedTabArchive();
  });
  panel.append(button, note);
  return panel;
}
