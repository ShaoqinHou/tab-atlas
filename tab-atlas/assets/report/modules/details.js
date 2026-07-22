function detailCategories(resource) {
  const values = [
    ["Space", resourceSpace(resource)],
    ["Topics", resourceTopics(resource).join(", ")],
    ["Focus", resourceFocuses(resource).join(", ")],
    ["Projects", resourceProjects(resource).join(", ")],
    ["Action lists", resourceActionLists(resource).join(", ")],
    ["Browser groups", contextGroupTitles(resource).join(", ")]
  ].filter(item => item[1]);
  if (!values.length) return null;
  const section = node("section", "detail-section");
  section.append(node("h3", "", "Context"));
  const list = node("dl", "detail-facts");
  for (const [label, value] of values) list.append(cue(label, value));
  section.append(list);
  return section;
}

function duplicateDetails(resource, duplicate) {
  const section = node("section", "detail-section duplicate-detail");
  section.append(
    node("h3", "", "Exact duplicates"),
    node("p", "detail-note", `${duplicate.safeCloseCandidates} safe close candidate${duplicate.safeCloseCandidates === 1 ? "" : "s"}; ${duplicate.protectedInstances} protected instance${duplicate.protectedInstances === 1 ? "" : "s"}.`)
  );
  const list = node("div", "duplicate-list");
  for (const group of exactDuplicateGroups(resource)) {
    const row = node("div", "duplicate-row");
    const label = `${capitalize(group.browser)}${group.groupTitle ? ` / ${group.groupTitle}` : ""}`;
    row.append(
      node("strong", "", label),
      node("span", "", `${group.tabs.length} exact tabs / ${group.safe} safe to close`)
    );
    list.append(row);
  }
  section.append(list);
  return section;
}

function metadataSection(resource) {
  const contexts = resourceContexts(resource);
  const openCount = liveTabs(resource).length;
  const section = node("section", "detail-section");
  section.append(node("h3", "", "Metadata"));
  const facts = node("dl", "metadata-grid");
  facts.append(
    cue("Source", resource.presentation?.source || resource.host),
    cue("Owner/site", resource.presentation?.publisher),
    cue("Format", resource.presentation?.format || resource.kind),
    cue("Intent", resource.presentation?.intent),
    cue("Open tabs", String(openCount)),
    cue("Captured in", unique(contexts.map(context => capitalize(context.browser)).filter(Boolean)).join(", ")),
    cue("Library state", openCount ? "Open in browser" : "Stored"),
    cue("First seen", formatDate(resource.firstSeenAt, true))
  );
  section.append(facts);
  return section;
}

function detailText(label, value) {
  const wrapper = node("div", "detail-text");
  wrapper.append(node("strong", "", label), node("p", "", value));
  return wrapper;
}

function instanceDisclosure(resource) {
  const contexts = resourceContexts(resource);
  if (!contexts.length) return null;
  const details = document.createElement("details");
  details.className = "detail-disclosure";
  details.append(node("summary", "", `Captured contexts (${contexts.length})`));
  const list = node("div", "instance-list");
  for (const context of contexts) {
    const item = node("div", "instance-row");
    const contextDetails = [];
    if (context.position !== null && context.position !== undefined && Number.isFinite(Number(context.position))) {
      contextDetails.push(`Position ${Number(context.position) + 1}`);
    }
    if (context.live === true) contextDetails.push("current capture");
    else if (context.live === false) contextDetails.push("stored capture");
    if (context.pinned) contextDetails.push("pinned");
    if (context.active) contextDetails.push("active");
    if (context.audible) contextDetails.push("audible");
    item.append(
      node("strong", "", `${capitalize(context.browser)}${context.groupTitle ? ` / ${context.groupTitle}` : ""}`),
      node("span", "", contextDetails.join(" / ") || "Captured browser context")
    );
    list.append(item);
  }
  details.append(list);
  return details;
}
