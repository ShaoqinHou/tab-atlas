// Discovery, inbox, duplicate, and open-tab review renderer.
function renderReview() {
  const openResources = openTabResources();
  const modes = [
    ["discoveries", "New discoveries", discoveries.length],
    ["dismissed", "Dismissed", dismissed.length],
    ["inbox", "Inbox", inboxResources().length],
    ["duplicates", "Exact duplicates", exactDuplicateResources().length],
    ["open", "Open tabs", currentTabCount()]
  ];
  const page = node("section", "review-page");
  const head = node("header", "review-head");
  const copy = node("div");
  copy.append(
    node("p", "eyebrow", "Library review"),
    node("h2", "", modes.find(item => item[0] === state.reviewMode)?.[1] || "Review"),
    node("p", "scope-summary", reviewDescription(state.reviewMode))
  );
  head.append(copy);
  const requestPanel = reviewActionPanel(state.reviewMode);
  if (requestPanel) head.append(requestPanel);
  page.append(head);

  const control = node("div", "review-modes");
  control.setAttribute("aria-label", "Review mode");
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

  let reviewResources = state.reviewMode === "discoveries" ? discoveries : inboxResources();
  if (state.reviewMode === "dismissed") reviewResources = dismissed;
  if (state.reviewMode === "duplicates") reviewResources = exactDuplicateResources();
  if (state.reviewMode === "open") reviewResources = openResources;
  let galleryLabel = `${formatNumber(reviewResources.length)} resource${reviewResources.length === 1 ? "" : "s"}`;
  if (state.reviewMode === "discoveries") {
    galleryLabel = `${formatNumber(reviewResources.length)} awaiting approval`;
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

function reviewActionPanel(mode) {
  if (!["discoveries", "duplicates", "open"].includes(mode)) return null;
  const duplicateCount = safeDuplicateCount();
  const openTabs = currentTabCount();
  const pending = pendingDiscoveryCount();
  const dismissedOpen = inventoryCount("currentDismissedResources", 0);
  const panel = node("div", "review-action");
  let label = openTabs ? "Close all captured tabs" : "All captured tabs closed";
  let disabled = openTabs === 0;
  if (mode === "discoveries") {
    label = pending ? "Accept all new discoveries" : "No new discoveries";
    disabled = pending === 0;
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
      : (mode === "open" && dismissedOpen
          ? `${formatNumber(dismissedOpen)} dismissed resource${dismissedOpen === 1 ? "" : "s"} will be discarded; accepted resources remain in the library. ${REQUEST_SAFETY_TEXT}`
          : REQUEST_SAFETY_TEXT)
  );
  note.id = "reviewActionSafety";
  button.type = "button";
  button.disabled = disabled;
  button.title = mode === "discoveries" && workspace.interactive
    ? "Updates the local library only; browser tabs are unchanged."
    : "Downloads a privacy-safe action request; it does not close tabs directly.";
  button.setAttribute("aria-describedby", note.id);
  button.addEventListener("click", () => {
    if (mode === "discoveries") requestDiscoveryAcceptance();
    else if (mode === "duplicates") requestDuplicateClose();
    else requestCapturedTabArchive();
  });
  panel.append(button, note);
  return panel;
}
