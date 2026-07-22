import { KEYS, PROTOCOL_VERSION } from "./config.js";

export function signedHeaders(browser, extensionId, nonce, auth) {
  return {
    "x-tabatlas-browser": browser,
    "x-tabatlas-extension": extensionId,
    "x-tabatlas-nonce": nonce,
    "x-tabatlas-auth": auth,
    "x-tabatlas-protocol": String(PROTOCOL_VERSION)
  };
}

export async function stillEnabled(key) {
  const stored = await chrome.storage.local.get([KEYS.mode, KEYS.key]);
  return stored[KEYS.mode] === "on" && stored[KEYS.key] === key;
}

export async function updateBadge() {
  const stored = await chrome.storage.local.get([KEYS.mode]);
  const on = stored[KEYS.mode] === "on";
  await chrome.action.setBadgeBackgroundColor({ color: on ? "#147d64" : "#667085" });
  await chrome.action.setBadgeText({ text: on ? "ON" : "" });
}

export async function flashBadge(text, color) {
  await chrome.action.setBadgeBackgroundColor({ color });
  await chrome.action.setBadgeText({ text });
  setTimeout(() => updateBadge().catch(() => {}), 2500);
}

export async function recordError(error) {
  await chrome.storage.local.set({ [KEYS.lastError]: safeError(error) });
  await flashBadge("!", "#b42318").catch(() => {});
}

export async function readJson(response) {
  return response.json().catch(() => ({}));
}

export function safeError(error) {
  return error instanceof Error ? error.message.slice(0, 240) : String(error).slice(0, 240);
}

export function inferBrowser() {
  const ua = navigator.userAgent;
  if (ua.includes("Edg/")) return "edge";
  if (ua.includes("Chrome/")) return "chrome";
  return "chromium";
}
