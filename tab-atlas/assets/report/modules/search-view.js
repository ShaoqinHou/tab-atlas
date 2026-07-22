// Full-catalog search renderer.
function renderSearch() {
  const matches = allResources.filter(matchesSearch).sort(resourceSort);
  const page = node("section", "search-page");
  const head = node("header", "search-head");
  const copy = node("div");
  copy.append(
    node("p", "eyebrow", "Global search"),
    node("h2", "", "Search results"),
    node("p", "scope-summary", `${formatNumber(matches.length)} match${matches.length === 1 ? "" : "es"} for "${elements.search.value.trim()}".`)
  );
  const clear = node("button", "secondary-command", "Clear search");
  clear.type = "button";
  clear.addEventListener("click", clearSearch);
  head.append(copy, clear);
  page.append(head, resourceGallery(matches, {
    label: "Matching resources",
    empty: "No resources match this search."
  }));
  elements.screen.replaceChildren(page);
}
