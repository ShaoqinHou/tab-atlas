import { inferBrowser } from "./platform.js";

export async function collectSnapshot(requestId, trigger) {
  const [windows, groups] = await Promise.all([
    chrome.windows.getAll({ populate: true, windowTypes: ["normal"] }),
    chrome.tabGroups.query({})
  ]);
  const capturedAt = new Date().toISOString();
  const browser = inferBrowser();
  const tabs = windows.flatMap((windowInfo) => (windowInfo.tabs || []).map((tab) => ({
    id: tab.id,
    windowId: tab.windowId,
    index: tab.index,
    groupId: tab.groupId,
    active: Boolean(tab.active),
    highlighted: Boolean(tab.highlighted),
    pinned: Boolean(tab.pinned),
    audible: Boolean(tab.audible),
    muted: Boolean(tab.mutedInfo?.muted),
    discarded: Boolean(tab.discarded),
    autoDiscardable: Boolean(tab.autoDiscardable),
    incognito: Boolean(tab.incognito),
    title: String(tab.title || ""),
    favIconUrl: String(tab.favIconUrl || ""),
    url: String(tab.url || tab.pendingUrl || ""),
    pendingUrl: String(tab.pendingUrl || "")
  })));
  return {
    schemaVersion: 1,
    requestId,
    trigger,
    browser,
    extensionId: chrome.runtime.id,
    capturedAt,
    windows: windows.map((windowInfo) => ({
      id: windowInfo.id,
      focused: Boolean(windowInfo.focused),
      incognito: Boolean(windowInfo.incognito),
      state: String(windowInfo.state || "normal"),
      type: String(windowInfo.type || "normal"),
      tabCount: (windowInfo.tabs || []).length
    })),
    groups: groups.map((group) => ({
      id: group.id,
      windowId: group.windowId,
      title: String(group.title || ""),
      color: String(group.color || "grey"),
      collapsed: Boolean(group.collapsed),
      shared: Boolean(group.shared)
    })),
    tabs
  };
}
