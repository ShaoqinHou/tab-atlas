import { KEYS } from "./config.js";

export async function clearArchiveControl(tabId) {
  const stored = await chrome.storage.local.get([KEYS.archiveControl]);
  if (Number(stored[KEYS.archiveControl]?.tabId) === Number(tabId)) {
    await chrome.storage.local.remove([KEYS.archiveControl]);
  }
}

export async function removeArchiveControl(tabId, windowId) {
  const tab = await chrome.tabs.get(tabId).catch(() => null);
  const expectedUrl = chrome.runtime.getURL("archive_complete.html");
  const actualUrl = String(tab?.url || tab?.pendingUrl || "");
  if (tab && Number(tab.windowId) === Number(windowId) && actualUrl === expectedUrl) {
    await chrome.tabs.remove(tabId).catch(() => {});
  }
  await clearArchiveControl(tabId);
}

export async function cleanupStaleArchiveControl() {
  const stored = await chrome.storage.local.get([KEYS.archiveControl]);
  const control = stored[KEYS.archiveControl];
  const tabId = Number(control?.tabId);
  const windowId = Number(control?.windowId);
  if (Number.isInteger(tabId) && Number.isInteger(windowId)) {
    await removeArchiveControl(tabId, windowId);
  } else if (control) {
    await chrome.storage.local.remove([KEYS.archiveControl]);
  }
}
