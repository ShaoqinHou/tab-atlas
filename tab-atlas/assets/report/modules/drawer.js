// Selected-resource inspector composition.
function renderDrawer() {
  const resource = state.selectedResource ? resourceById.get(state.selectedResource) : null;
  if (!resource) {
    elements.drawerHost.replaceChildren();
    elements.topbar.inert = false;
    elements.primaryNav.inert = false;
    elements.screen.inert = false;
    document.body.classList.remove("drawer-open");
    return;
  }

  elements.topbar.inert = true;
  elements.primaryNav.inert = true;
  elements.screen.inert = true;
  document.body.classList.add("drawer-open");
  const backdrop = node("div", "drawer-backdrop");
  backdrop.addEventListener("click", event => {
    if (event.target === backdrop) closeDetails();
  });
  const drawer = node("aside", "detail-drawer");
  drawer.dataset.resourceId = resource.resourceId;
  drawer.setAttribute("role", "dialog");
  drawer.setAttribute("aria-modal", "true");
  drawer.setAttribute("aria-label", `Details for ${resourceDisplayTitle(resource)}`);

  const top = node("header", "drawer-top");
  top.append(node("span", "drawer-kicker", `${resource.presentation?.source || resource.host || "Source"} / ${resource.presentation?.format || resource.kind || "Resource"}`));
  const close = node("button", "drawer-close");
  close.type = "button";
  close.innerHTML = "&times;";
  close.title = "Close details";
  close.setAttribute("aria-label", "Close details");
  close.addEventListener("click", closeDetails);
  top.append(close);
  drawer.append(top, interactivePreviewSurface(resource, "detail"));

  const content = node("div", "drawer-content");
  const titleBlock = node("section", "detail-title");
  titleBlock.append(node("h2", "", resourceDisplayTitle(resource)));
  titleBlock.append(resourceCommands(resource, "drawer"));
  content.append(titleBlock);

  content.append(resourceNoteSection(resource));
  if (workspace.interactive && !workspace.noteCache.has(resource.resourceId)) {
    loadResourceWorkspaceState(resource.resourceId);
  }
  const actionProgress = resourceActionProgressSection(resource);
  if (actionProgress) content.append(actionProgress);

  const glance = node("section", "detail-section");
  glance.append(
    node("h3", "", "At a glance"),
    node(
      "p",
      "detail-brief",
      resource.brief || resource.presentation?.contextCue || `${resource.presentation?.format || resource.kind || "Resource"} from ${resource.presentation?.source || resource.host || "the captured source"}.`
    )
  );
  const cues = node("dl", "cue-list");
  cues.append(
    cue("Relevance", resource.presentation?.contextCue),
    cue("Next", resource.presentation?.decisionCue)
  );
  glance.append(cues);
  content.append(glance);

  const categories = detailCategories(resource);
  if (categories) content.append(categories);
  const duplicate = duplicateSummary(resource);
  if (duplicate.sets) content.append(duplicateDetails(resource, duplicate));
  content.append(metadataSection(resource));

  if (resource.whyKept || resource.detail) {
    const more = document.createElement("details");
    more.className = "detail-disclosure";
    more.append(node("summary", "", "More context"));
    if (resource.whyKept) more.append(detailText("Why it was kept", resource.whyKept));
    if (resource.detail) more.append(detailText("Details", resource.detail));
    content.append(more);
  }
  const contexts = instanceDisclosure(resource);
  if (contexts) content.append(contexts);
  drawer.append(content, resourceFooter(resource, "drawer"));
  backdrop.append(drawer);
  elements.drawerHost.replaceChildren(backdrop);
  requestAnimationFrame(() => close.focus({ preventScroll: true }));
}
