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
  body.append(title, node("p", "card-brief", resource.brief || "No concise description yet."));

  const cueText = presentation.decisionCue || presentation.contextCue || "Review whether this resource still supports current work.";
  const cue = node("p", "card-cue");
  cue.append(node("span", "", "Next"), document.createTextNode(` ${cueText}`));
  body.append(cue);

  const chips = resourceChips(resource, options.hideSpace);
  if (chips.childElementCount) body.append(chips);
  article.append(body, resourceCommands(resource, "card"), resourceFooter(resource, "card"));
  return article;
}

function previewVisual(resource, size, allowRemote) {
  const presentation = resource.presentation || {};
  const preview = presentation.preview || {};
  const frame = node("div", `preview-frame ${size} accent-${preview.accent || "teal"}`);
  const fallback = sourceFallback(resource);
  const localImage = String(preview.localImage || "");
  if (localImage) {
    const image = document.createElement("img");
    image.className = "preview-image";
    image.alt = `Preview for ${resourceDisplayTitle(resource)}`;
    image.loading = size === "detail" ? "eager" : "lazy";
    image.decoding = "async";
    image.addEventListener("error", () => frame.replaceChildren(fallback));
    image.src = localImage;
    frame.append(
      image,
      node("span", "preview-evidence-label", preview.evidenceLabel || "Saved image")
    );
    return frame;
  }

  frame.append(fallback);
  const remoteImage = String(preview.remoteImage || "");
  if (allowRemote && /^https:\/\//i.test(remoteImage)) {
    const load = node("button", "remote-load", "Load remote preview");
    load.type = "button";
    load.title = "Loads this preview from its remote source only after this click.";
    load.addEventListener("click", () => {
      load.disabled = true;
      load.textContent = "Loading preview";
      const image = document.createElement("img");
      image.className = "preview-image";
      image.alt = `Preview for ${resourceDisplayTitle(resource)}`;
      image.referrerPolicy = "no-referrer";
      image.addEventListener("load", () => frame.replaceChildren(image));
      image.addEventListener("error", () => {
        load.disabled = false;
        load.textContent = "Preview unavailable";
        frame.replaceChildren(fallback, load);
      });
      image.src = remoteImage;
    });
    frame.append(load);
  }
  return frame;
}

function interactivePreviewSurface(resource, size, openDetails = null) {
  const surface = node("div", `preview-surface ${size}`);
  const frame = previewVisual(resource, size, size === "detail");
  const motion = normalizedMotionPreview(resource);
  surface.append(frame);

  let detailsButton = null;
  if (openDetails) {
    detailsButton = node("button", "preview-open-details");
    detailsButton.type = "button";
    detailsButton.setAttribute("aria-label", `Open details for ${resourceDisplayTitle(resource)}`);
    detailsButton.addEventListener("click", openDetails);
    surface.append(detailsButton);
  }

  if (!motion) return surface;
  const trigger = node("button", "motion-preview-trigger");
  trigger.type = "button";
  trigger.title = motion.label;
  trigger.setAttribute("aria-label", motion.label);
  trigger.append(node("span", "motion-play-icon"));
  surface.append(trigger);

  trigger.addEventListener("click", event => {
    event.preventDefault();
    event.stopPropagation();
    if (activeMotionPreview?.surface === surface) stopActiveMotionPreview();
    else startMotionPreview(surface, resource, motion, size, detailsButton, trigger);
  });
  surface.addEventListener("pointerenter", event => {
    if (event.pointerType === "touch" || !automaticMotionAllowed()) return;
    scheduleMotionLoad(surface, resource, motion, size, detailsButton, trigger);
  });
  surface.addEventListener("pointerleave", () => {
    cancelMotionLoad(surface);
    if (activeMotionPreview?.surface === surface) stopActiveMotionPreview();
  });
  return surface;
}

function normalizedMotionPreview(resource) {
  const motion = resource.presentation?.preview?.motion || {};
  if (motion.kind === "youtube" && /^[A-Za-z0-9_-]{6,32}$/.test(String(motion.videoId || ""))) {
    if (!/^https?:$/.test(window.location.protocol)) return null;
    return {
      kind: "youtube",
      videoId: String(motion.videoId),
      origin: "https://www.youtube-nocookie.com",
      label: String(motion.label || "Play video preview")
    };
  }
  if (motion.kind !== "x_mp4") return null;
  try {
    const url = new URL(String(motion.url || ""));
    const allowedPath = /^\/(?:amplify_video|ext_tw_video|tweet_video)\//i.test(url.pathname);
    if (url.protocol !== "https:" || url.hostname !== "video.twimg.com" || url.port || !allowedPath || !/\.mp4$/i.test(url.pathname)) {
      return null;
    }
    return {
      kind: "x_mp4",
      url: url.href,
      origin: url.origin,
      label: String(motion.label || "Play post video preview")
    };
  } catch (_error) {
    return null;
  }
}

function automaticMotionAllowed() {
  const reducedMotion = window.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches;
  return !reducedMotion && !navigator.connection?.saveData;
}

function scheduleMotionLoad(surface, resource, motion, size, detailsButton, trigger) {
  cancelMotionLoad(surface);
  surface._motionPreconnectTimer = motionTimer(140, () => ensureConnectionHint(motion.origin));
  surface._motionStartTimer = motionTimer(480, () => {
    surface._motionStartTimer = null;
    if (surface.isConnected) {
      startMotionPreview(surface, resource, motion, size, detailsButton, trigger);
    }
  });
}

function motionTimer(delay, callback) {
  const timer = window.setTimeout(() => {
    motionLoadTimers.delete(timer);
    callback();
  }, delay);
  motionLoadTimers.add(timer);
  return timer;
}

function cancelMotionLoad(surface) {
  for (const key of ["_motionPreconnectTimer", "_motionStartTimer"]) {
    const timer = surface[key];
    if (!timer) continue;
    window.clearTimeout(timer);
    motionLoadTimers.delete(timer);
    surface[key] = null;
  }
}

function ensureConnectionHint(origin) {
  if (!origin || connectionHints.has(origin)) return;
  const hint = document.createElement("link");
  hint.rel = "preconnect";
  hint.href = origin;
  hint.crossOrigin = "anonymous";
  document.head.append(hint);
  connectionHints.add(origin);
}

function startMotionPreview(surface, resource, motion, size, detailsButton, trigger) {
  if (!surface.isConnected || activeMotionPreview?.surface === surface) return;
  stopActiveMotionPreview();
  cancelMotionLoad(surface);
  ensureConnectionHint(motion.origin);

  const layer = node("div", `motion-preview-layer motion-${motion.kind}`);
  layer.setAttribute("aria-label", `Live preview for ${resourceDisplayTitle(resource)}`);
  surface.classList.add("is-motion-active");
  if (detailsButton) detailsButton.hidden = true;
  trigger.hidden = true;
  surface.append(layer);

  let media = null;
  let removeMediaListeners = () => {};
  const stop = () => {
    removeMediaListeners();
    if (media instanceof HTMLVideoElement) {
      media.pause();
      media.removeAttribute("src");
      media.load();
    } else if (media instanceof HTMLIFrameElement) {
      media.removeAttribute("srcdoc");
      media.src = "about:blank";
    }
    layer.remove();
    surface.classList.remove("is-motion-active", "is-motion-playing");
    if (detailsButton) detailsButton.hidden = false;
    trigger.hidden = false;
  };
  activeMotionPreview = { surface, stop };

  if (motion.kind === "x_mp4") {
    const iframe = document.createElement("iframe");
    media = iframe;
    iframe.className = "motion-preview-player";
    iframe.title = `Video preview for ${resourceDisplayTitle(resource)}`;
    iframe.allow = "autoplay; picture-in-picture";
    iframe.setAttribute("sandbox", "allow-scripts");
    const receiveMotionState = event => {
      if (event.source !== iframe.contentWindow || event.data?.source !== "tab-atlas-motion-preview") return;
      if (event.data.state === "playing") surface.classList.add("is-motion-playing");
      if (event.data.state === "error") failMotionPreview(surface);
    };
    window.addEventListener("message", receiveMotionState);
    removeMediaListeners = () => window.removeEventListener("message", receiveMotionState);
    iframe.srcdoc = xMotionDocument(motion.url, size === "detail");
    layer.append(iframe);
    return;
  }

  const iframe = document.createElement("iframe");
  media = iframe;
  iframe.className = "motion-preview-player";
  iframe.title = `Video preview for ${resourceDisplayTitle(resource)}`;
  iframe.allow = "autoplay; encrypted-media; picture-in-picture";
  iframe.referrerPolicy = "strict-origin-when-cross-origin";
  iframe.addEventListener("load", () => surface.classList.add("is-motion-playing"));
  iframe.addEventListener("error", () => failMotionPreview(surface));
  const controls = size === "detail" ? "1" : "0";
  iframe.src = `${motion.origin}/embed/${motion.videoId}?autoplay=1&mute=1&controls=${controls}&playsinline=1&rel=0`;
  layer.append(iframe);
}

function xMotionDocument(url, controls) {
  const safeUrl = String(url)
    .replaceAll("&", "&amp;")
    .replaceAll('"', "&quot;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;");
  const controlsAttribute = controls ? " controls" : "";
  return `<!doctype html>
<html><head>
<meta name="referrer" content="no-referrer">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; media-src https://video.twimg.com; style-src 'unsafe-inline'; script-src 'unsafe-inline'">
<style>html,body,video{width:100%;height:100%;margin:0;background:#000}video{display:block;object-fit:cover}</style>
</head><body>
<video autoplay muted loop playsinline preload="metadata"${controlsAttribute} src="${safeUrl}"></video>
<script>
const video=document.querySelector("video");
const notify=state=>parent.postMessage({source:"tab-atlas-motion-preview",state},"*");
video.addEventListener("playing",()=>notify("playing"),{once:true});
video.addEventListener("error",()=>notify("error"),{once:true});
video.play().catch(()=>{video.controls=true});
</script>
</body></html>`;
}

function failMotionPreview(surface) {
  if (activeMotionPreview?.surface !== surface) return;
  stopActiveMotionPreview();
  elements.actionStatus.textContent = "Live preview unavailable; the saved preview remains available.";
}

function stopActiveMotionPreview() {
  if (!activeMotionPreview) return;
  const current = activeMotionPreview;
  activeMotionPreview = null;
  current.stop();
}

function sourceFallback(resource) {
  const presentation = resource.presentation || {};
  const fallback = node("div", "source-fallback");
  fallback.setAttribute("aria-hidden", "true");
  const identity = presentation.publisher || presentation.source || resource.host || "Resource";
  const summary = resource.brief || presentation.contextCue || resource.displayUrl || "Stored page metadata";
  fallback.append(
    node("span", "fallback-source", identity),
    node("strong", "fallback-title", resourceDisplayTitle(resource)),
    node("span", "fallback-summary", summary),
    node("span", "fallback-meta", `${presentation.format || resource.kind || "Resource"} / ${presentation.intent || "Reference"}`),
    node(
      "span",
      "preview-evidence-label",
      presentation.preview?.metadataLabel || "Metadata preview"
    )
  );
  return fallback;
}

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
  glance.append(node("h3", "", "At a glance"), node("p", "detail-brief", resource.brief || "No concise description yet."));
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
