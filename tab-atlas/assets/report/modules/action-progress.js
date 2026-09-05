// Action-list progress rendering and persistence.
function resourceActionProgressSection(resource) {
  const memberships = (resource.collections || []).filter(item => item.kind === "action_list");
  if (!memberships.length) return null;
  const byCollection = new Map((resource.actionItems || []).map(item => [item.collectionId, item]));
  const section = node("section", "detail-section action-progress-section");
  section.append(node("h3", "", "Action progress"));
  for (const membership of memberships) {
    const existing = byCollection.get(membership.id) || {};
    const item = {
      collectionId: membership.id,
      collectionName: membership.name,
      workflowKind: membership.workflowKind || "none",
      state: existing.state || "queued",
      priority: Number(existing.priority || 3),
      completedUnits: Number(existing.completedUnits || 0),
      totalUnits: existing.totalUnits == null ? null : Number(existing.totalUnits),
      dueAt: existing.dueAt || "",
      revision: Number(existing.revision || 0)
    };
    section.append(actionProgressEditor(resource, item));
  }
  return section;
}

function actionProgressEditor(resource, item) {
  const form = node("form", "action-progress-editor");
  const heading = node("div", "action-progress-heading");
  heading.append(
    node("strong", "", item.collectionName),
    node("span", "", actionProgressSummary(item))
  );
  form.append(heading);

  const fields = node("div", "action-progress-fields");
  const statusLabel = node("label", "action-progress-field");
  statusLabel.append(node("span", "", "Status"));
  const status = document.createElement("select");
  for (const [value, label] of [
    ["queued", "Queued"],
    ["in_progress", "In progress"],
    ["completed", "Completed"],
    ["snoozed", "Snoozed"],
    ["skipped", "Skipped"]
  ]) {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = label;
    option.selected = item.state === value;
    status.append(option);
  }
  statusLabel.append(status);

  const priorityLabel = node("label", "action-progress-field");
  priorityLabel.append(node("span", "", "Priority"));
  const priority = document.createElement("select");
  for (let value = 1; value <= 5; value += 1) {
    const option = document.createElement("option");
    option.value = String(value);
    option.textContent = value === 1 ? "1 / highest" : String(value);
    option.selected = item.priority === value;
    priority.append(option);
  }
  priorityLabel.append(priority);

  const unitName = ["watch_queue", "reading_queue"].includes(item.workflowKind) ? "Minutes" : "Completed";
  const completedLabel = node("label", "action-progress-field");
  completedLabel.append(node("span", "", unitName));
  const completed = document.createElement("input");
  completed.type = "number";
  completed.min = "0";
  completed.max = "100000";
  completed.step = "1";
  completed.value = String(item.completedUnits);
  completedLabel.append(completed);

  const totalLabel = node("label", "action-progress-field");
  totalLabel.append(node("span", "", "Total"));
  const total = document.createElement("input");
  total.type = "number";
  total.min = "0";
  total.max = "100000";
  total.step = "1";
  total.placeholder = "Optional";
  if (item.totalUnits != null) total.value = String(item.totalUnits);
  totalLabel.append(total);
  fields.append(statusLabel, priorityLabel, completedLabel, totalLabel);
  form.append(fields);

  if (workspace.interactive) {
    const save = node("button", "secondary-command action-progress-save", "Save progress");
    save.type = "submit";
    form.append(save);
    form.addEventListener("submit", async event => {
      event.preventDefault();
      save.disabled = true;
      try {
        const totalUnits = total.value === "" ? null : Number(total.value);
        let completedUnits = Number(completed.value || 0);
        if (status.value === "completed" && totalUnits != null) completedUnits = totalUnits;
        const result = await workspaceRequest(
          `/api/v1/resources/${resource.resourceId}/action-lists/${item.collectionId}/progress`,
          {
            method: "POST",
            json: {
              state: status.value,
              priority: Number(priority.value),
              completedUnits,
              totalUnits,
              dueAt: item.dueAt,
              expectedRevision: item.revision
            }
          }
        );
        if (result.auditId) workspace.latestAuditByResource.set(resource.resourceId, result.auditId);
        await loadResourceWorkspaceState(resource.resourceId, true);
        await refreshWorkspaceSession();
        elements.actionStatus.textContent = "Action progress saved.";
        render();
      } catch (error) {
        showWorkspaceError(error, "Action progress could not be saved.");
      } finally {
        save.disabled = false;
      }
    });
  } else {
    for (const control of fields.querySelectorAll("select,input")) control.disabled = true;
  }
  return form;
}

function actionProgressSummary(item) {
  const label = ({ queued: "Queued", in_progress: "In progress", completed: "Completed", snoozed: "Snoozed", skipped: "Skipped" })[item.state] || "Queued";
  if (item.totalUnits == null) return label;
  return `${label} / ${formatNumber(item.completedUnits)} of ${formatNumber(item.totalUnits)}`;
}
