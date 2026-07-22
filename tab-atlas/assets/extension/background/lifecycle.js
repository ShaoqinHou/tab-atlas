import { keyFromToken } from "../protocol.js";
import { cleanupStaleArchiveControl } from "./archive_control.js";
import { KEYS, POLL_ALARM, POLL_MINUTES, PROTOCOL_VERSION } from "./config.js";
import { inferBrowser, recordError, safeError, updateBadge } from "./platform.js";

export async function initialize() {
  const stored = await chrome.storage.local.get([KEYS.mode, KEYS.key, KEYS.legacyToken]);
  if (!stored[KEYS.key] && stored[KEYS.legacyToken]) {
    await chrome.storage.local.set({ [KEYS.key]: await keyFromToken(stored[KEYS.legacyToken]) });
    await chrome.storage.local.remove([KEYS.legacyToken]);
  }
  if (stored[KEYS.mode] !== "on" && stored[KEYS.mode] !== "off") {
    await chrome.storage.local.set({ [KEYS.mode]: "off" });
  }
  await cleanupStaleArchiveControl();
  await synchronizeAlarm();
}

export async function getStatus() {
  const stored = await chrome.storage.local.get(Object.values(KEYS));
  const alarm = await chrome.alarms.get(POLL_ALARM);
  return {
    ok: true,
    version: chrome.runtime.getManifest().version,
    protocolVersion: PROTOCOL_VERSION,
    mode: stored[KEYS.mode] === "on" ? "on" : "off",
    paired: typeof stored[KEYS.key] === "string" && stored[KEYS.key].length === 64,
    browser: inferBrowser(),
    pairedBrowser: stored[KEYS.pairedBrowser] || "",
    alarmActive: Boolean(alarm),
    lastCaptureAt: stored[KEYS.lastCaptureAt] || "",
    lastError: stored[KEYS.lastError] || ""
  };
}

export async function setMode(mode, pollReceiver, abortPolling) {
  if (mode === "on") {
    const stored = await chrome.storage.local.get([KEYS.key]);
    if (typeof stored[KEYS.key] !== "string" || stored[KEYS.key].length !== 64) {
      return { ...(await getStatus()), ok: false, error: "Pair this browser before turning capture on." };
    }
  } else {
    abortPolling();
  }
  await chrome.storage.local.set({ [KEYS.mode]: mode, [KEYS.lastError]: "" });
  await synchronizeAlarm();
  if (mode === "on") {
    try {
      await pollReceiver("enabled");
    } catch (error) {
      await recordError(error);
      return { ...(await getStatus()), ok: false, error: safeError(error) };
    }
  }
  await updateBadge();
  return getStatus();
}

export async function synchronizeAlarm() {
  const stored = await chrome.storage.local.get([KEYS.mode]);
  if (stored[KEYS.mode] === "on") {
    const current = await chrome.alarms.get(POLL_ALARM);
    if (!current || current.periodInMinutes !== POLL_MINUTES) {
      await chrome.alarms.create(POLL_ALARM, {
        delayInMinutes: POLL_MINUTES,
        periodInMinutes: POLL_MINUTES
      });
    }
  } else {
    await chrome.alarms.clear(POLL_ALARM);
  }
  await updateBadge();
}
