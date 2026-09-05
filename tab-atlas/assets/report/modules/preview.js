// Static, remote-on-demand, and hover motion preview surfaces.
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
  const frame = previewVisual(resource, size, size !== "mosaic");
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
