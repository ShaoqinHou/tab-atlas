// Render lifecycle, lazy-gallery observers, freshness, and primary navigation state.
function render() {
  stopActiveMotionPreview();
  clearMotionLoadTimers();
  disconnectGalleryObservers();
  updateNavigation();
  if (state.search) renderSearch();
  else if (state.view === "spaces") renderSpaces();
  else if (state.view === "sources") renderSources();
  else if (state.view === "review") renderReview();
  else renderHome();
  renderDrawer();
  renderAgentPanel();
  connectGalleryObservers();
}

function clearMotionLoadTimers() {
  for (const timer of motionLoadTimers) window.clearTimeout(timer);
  motionLoadTimers.clear();
}

function disconnectGalleryObservers() {
  for (const observer of galleryObservers) observer.disconnect();
  galleryObservers.clear();
  for (const timer of galleryLoadTimers) window.clearTimeout(timer);
  galleryLoadTimers.clear();
}

function connectGalleryObservers() {
  if (typeof window.IntersectionObserver !== "function") return;
  for (const sentinel of elements.screen.querySelectorAll("[data-gallery-sentinel]")) {
    const loadNextBatch = galleryLoaders.get(sentinel);
    if (!loadNextBatch) continue;
    const observer = new IntersectionObserver(entries => {
      if (!entries.some(entry => entry.isIntersecting)) return;
      observer.unobserve(sentinel);
      sentinel.classList.add("is-loading");
      sentinel.setAttribute("aria-busy", "true");
      const status = sentinel.querySelector(".gallery-sentinel-status");
      const batchSize = Number(sentinel.dataset.nextBatchSize) || PAGE_SIZE;
      if (status) status.textContent = `Loading ${formatNumber(batchSize)} more resources`;

      const timer = window.setTimeout(() => {
        galleryLoadTimers.delete(timer);
        if (!galleryObservers.has(observer) || !sentinel.isConnected) return;
        if (loadNextBatch()) {
          observer.observe(sentinel);
        } else {
          observer.disconnect();
          galleryObservers.delete(observer);
        }
      }, 0);
      galleryLoadTimers.add(timer);
    }, { root: null, rootMargin: "720px 0px", threshold: 0 });
    galleryObservers.add(observer);
    observer.observe(sentinel);
  }
}

function renderFreshness() {
  const captures = data.inventory?.captures || [];
  elements.freshness.textContent = captures.length
    ? captures.map(item => `${capitalize(item.browser)} ${formatDate(item.capturedAt)}`).join(" / ")
    : "No capture imported yet";
}

function updateNavigation() {
  for (const button of elements.primaryNav.querySelectorAll("button[data-view]")) {
    const selected = !state.search && (
      button.dataset.view === state.view
      || (button.dataset.view === "library" && ["spaces", "sources"].includes(state.view))
    );
    button.classList.toggle("active", selected);
    button.setAttribute("aria-selected", String(selected));
  }
  const attentionCount = pendingDiscoveryCount() || currentTabCount();
  elements.navLiveCount.textContent = String(attentionCount);
  elements.navLiveCount.hidden = attentionCount === 0;
}
