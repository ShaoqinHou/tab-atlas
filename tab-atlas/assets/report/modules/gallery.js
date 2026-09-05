// Paginated resource gallery and card composition.
function resourceGallery(items, options = {}) {
  const section = node("section", "gallery-section");
  const heading = node("header", "gallery-head");
  const visibleCount = Math.min(items.length, state.visibleLimit);
  const count = node("span", "gallery-count", `${formatNumber(visibleCount)} of ${formatNumber(items.length)}`);
  heading.append(
    node("h3", "", options.label || "Resources"),
    count
  );
  section.append(heading);
  if (!items.length) {
    section.append(emptyState(options.empty || "No resources in this view."));
    return section;
  }

  const grid = node("div", "resource-grid");
  for (const resource of items.slice(0, visibleCount)) {
    grid.append(resourceCard(resource, options));
  }
  section.append(grid);
  if (visibleCount < items.length) {
    let renderedCount = visibleCount;
    const sentinel = node("div", "gallery-sentinel");
    const status = node("span", "gallery-sentinel-status");
    sentinel.dataset.gallerySentinel = "";
    sentinel.setAttribute("role", "status");
    sentinel.setAttribute("aria-live", "polite");
    sentinel.setAttribute("aria-atomic", "true");
    sentinel.setAttribute("aria-busy", "false");

    const updateSentinel = () => {
      const remaining = items.length - renderedCount;
      const nextBatchSize = Math.min(PAGE_SIZE, remaining);
      sentinel.dataset.nextBatchSize = String(nextBatchSize);
      status.textContent = `${formatNumber(remaining)} more resource${remaining === 1 ? "" : "s"} available`;
    };
    updateSentinel();
    sentinel.append(status);
    section.append(sentinel);

    galleryLoaders.set(sentinel, () => {
      const nextCount = Math.min(renderedCount + PAGE_SIZE, items.length);
      const fragment = document.createDocumentFragment();
      for (const resource of items.slice(renderedCount, nextCount)) {
        fragment.append(resourceCard(resource, options));
      }
      grid.append(fragment);
      renderedCount = nextCount;
      state.visibleLimit = Math.max(state.visibleLimit, renderedCount);
      count.textContent = `${formatNumber(renderedCount)} of ${formatNumber(items.length)}`;
      sentinel.classList.remove("is-loading");
      sentinel.setAttribute("aria-busy", "false");
      if (renderedCount >= items.length) {
        sentinel.classList.add("is-complete");
        sentinel.removeAttribute("data-next-batch-size");
        status.textContent = `All ${formatNumber(items.length)} resources loaded`;
        return false;
      }
      updateSentinel();
      return true;
    });
  }
  return section;
}

function resourceCard(resource, options = {}) {
  const presentation = resource.presentation || {};
  const article = node("article", "resource-card");
  article.dataset.resourceId = resource.resourceId;

  article.append(interactivePreviewSurface(
    resource,
    "card",
    event => openDetails(resource.resourceId, event.currentTarget)
  ));

  const body = node("div", "card-body");
  body.append(node("p", "card-source", `${presentation.source || resource.host || "Unknown source"} / ${presentation.format || resource.kind || "Resource"}`));
  const title = node("h3", "resource-title");
  const titleButton = node("button", "card-title-button", resourceDisplayTitle(resource));
  titleButton.type = "button";
  titleButton.addEventListener("click", event => openDetails(resource.resourceId, event.currentTarget));
  title.append(titleButton);
  body.append(
    title,
    node(
      "p",
      "card-brief",
      resource.brief || presentation.contextCue || `${presentation.format || resource.kind || "Resource"} from ${presentation.source || resource.host || "the captured source"}.`
    )
  );

  const cueText = presentation.decisionCue || presentation.contextCue || "Review whether this resource still supports current work.";
  const cue = node("p", "card-cue");
  cue.append(node("span", "", "Next"), document.createTextNode(` ${cueText}`));
  body.append(cue);

  const chips = resourceChips(resource, options.hideSpace);
  if (chips.childElementCount) body.append(chips);
  article.append(body, resourceCommands(resource, "card"), resourceFooter(resource, "card"));
  return article;
}
