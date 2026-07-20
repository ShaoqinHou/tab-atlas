import {
  archiveControlReason,
  archiveTargetReason,
  duplicateTargetReason,
  hmacHex,
  keyFromToken,
  protocolMessage,
  randomNonce,
  sha256Hex,
  verifyHmac
} from "./protocol.js";

const RECEIVER = "http://127.0.0.1:9786";
const PROTOCOL_VERSION = 4;
const POLL_ALARM = "tab-atlas-poll";
const POLL_MINUTES = 0.5;
const KEYS = {
  mode: "tabAtlasMode",
  key: "tabAtlasKey",
  legacyToken: "tabAtlasToken",
  pairedBrowser: "tabAtlasPairedBrowser",
  lastCaptureAt: "tabAtlasLastCaptureAt",
  lastError: "tabAtlasLastError",
  archiveControl: "tabAtlasArchiveControl"
};

let pollInFlight = null;
let pollAbortController = null;

chrome.runtime.onInstalled.addListener(() => initialize().catch(recordError));
chrome.runtime.onStartup.addListener(() => initialize().catch(recordError));

chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === POLL_ALARM) pollReceiver("alarm").catch(recordError);
});

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (!message || typeof message.type !== "string") return false;
  handleMessage(message)
    .then(sendResponse)
    .catch((error) => sendResponse({ ok: false, error: safeError(error) }));
  return true;
});

async function initialize() {
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

async function handleMessage(message) {
  switch (message.type) {
    case "tabatlas:status":
      return getStatus();
    case "tabatlas:set-mode":
      return setMode(message.mode === "on" ? "on" : "off");
    case "tabatlas:pair":
      return pair(String(message.code || "").trim().toUpperCase());
    case "tabatlas:capture-now":
      return pollReceiver("popup");
    default:
      return { ok: false, error: "unsupported message" };
  }
}

async function getStatus() {
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

async function setMode(mode) {
  if (mode === "on") {
    const stored = await chrome.storage.local.get([KEYS.key]);
    if (typeof stored[KEYS.key] !== "string" || stored[KEYS.key].length !== 64) {
      return { ...(await getStatus()), ok: false, error: "Pair this browser before turning capture on." };
    }
  } else if (pollAbortController) {
    pollAbortController.abort();
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

async function synchronizeAlarm() {
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

async function pair(code) {
  if (!/^[A-Z2-9]{8}$/.test(code)) {
    return { ok: false, error: "Enter the 8-character pairing code." };
  }
  const browser = inferBrowser();
  const nonce = randomNonce();
  const pairingKey = await keyFromToken(code);
  const clientProof = await hmacHex(
    pairingKey,
    protocolMessage("pair", browser, chrome.runtime.id, nonce)
  );
  const response = await fetch(`${RECEIVER}/v1/pair`, {
    method: "POST",
    cache: "no-store",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ browser, extensionId: chrome.runtime.id, nonce, clientProof })
  });
  const payload = await readJson(response);
  if (!response.ok || !payload.token) {
    throw new Error(payload.error || `Pairing failed (${response.status})`);
  }
  const key = await keyFromToken(payload.token);
  const receiverVerified = await verifyHmac(
    pairingKey,
    protocolMessage("paired", browser, chrome.runtime.id, nonce, key),
    payload.serverProof
  );
  if (!receiverVerified) throw new Error("Pairing receiver identity check failed.");
  await chrome.storage.local.set({
    [KEYS.key]: key,
    [KEYS.pairedBrowser]: browser,
    [KEYS.mode]: "on",
    [KEYS.lastError]: ""
  });
  await synchronizeAlarm();
  return { ok: true, browser };
}

async function pollReceiver(trigger) {
  if (pollInFlight) return pollInFlight;
  const controller = new AbortController();
  pollAbortController = controller;
  pollInFlight = pollReceiverOnce(trigger, controller.signal).finally(() => {
    if (pollAbortController === controller) pollAbortController = null;
    pollInFlight = null;
  });
  return pollInFlight;
}

async function pollReceiverOnce(trigger, signal) {
  const stored = await chrome.storage.local.get([KEYS.mode, KEYS.key]);
  if (stored[KEYS.mode] !== "on") return { ok: false, idle: true, reason: "off" };
  const key = typeof stored[KEYS.key] === "string" ? stored[KEYS.key] : "";
  if (!/^[a-f0-9]{64}$/.test(key)) return { ok: false, idle: true, reason: "unpaired" };

  const browser = inferBrowser();
  const extensionId = chrome.runtime.id;
  const nonce = randomNonce();
  const auth = await hmacHex(
    key,
    protocolMessage("command", browser, extensionId, nonce)
  );

  let response;
  try {
    response = await fetch(`${RECEIVER}/v1/command`, {
      method: "GET",
      cache: "no-store",
      signal,
      headers: signedHeaders(browser, extensionId, nonce, auth)
    });
  } catch (error) {
    if (signal.aborted) return { ok: false, idle: true, reason: "off" };
    return { ok: false, idle: true, reason: "receiver-offline" };
  }
  const command = await readJson(response);
  if (response.status === 401 || response.status === 403) {
    const revoked = command.action === "revoked" && await verifyHmac(
      key,
      protocolMessage("response", browser, extensionId, nonce, "", "revoked"),
      command.serverProof
    );
    if (revoked) {
      await chrome.storage.local.remove([KEYS.key, KEYS.pairedBrowser]);
      await chrome.storage.local.set({
        [KEYS.mode]: "off",
        [KEYS.lastError]: "Pairing was revoked. Pair again when needed."
      });
      await synchronizeAlarm();
      return { ok: false, error: "revoked" };
    }
    throw new Error("The receiver could not authenticate this pairing.");
  }
  if (!response.ok) throw new Error(command.error || `Unexpected receiver response (${response.status}).`);

  const requestId = typeof command.requestId === "string" ? command.requestId : "";
  const targetsHash = typeof command.targetsHash === "string" ? command.targetsHash : "";
  if (!new Set([
    "capture",
    "idle",
    "close_exact_duplicates",
    "archive_captured_tabs",
    "close_archive_control"
  ]).has(command.action)) {
    throw new Error("Receiver returned an unsupported command.");
  }
  const receiverVerified = await verifyHmac(
    key,
    protocolMessage("response", browser, extensionId, nonce, requestId, command.action, targetsHash),
    command.serverProof
  );
  if (!receiverVerified) throw new Error("Receiver identity check failed.");
  if (command.action === "close_exact_duplicates") {
    if (!(await stillEnabled(key))) return { ok: false, idle: true, reason: "off" };
    return executeExactDuplicateMutation(command, key, browser, extensionId, signal);
  }
  if (command.action === "archive_captured_tabs") {
    if (!(await stillEnabled(key))) return { ok: false, idle: true, reason: "off" };
    return executeArchiveMutation(command, key, browser, extensionId, signal);
  }
  if (command.action === "close_archive_control") {
    if (!(await stillEnabled(key))) return { ok: false, idle: true, reason: "off" };
    return executeArchiveControlCleanup(command, key, browser, extensionId, signal);
  }
  if (command.action !== "capture") {
    await chrome.storage.local.set({ [KEYS.lastError]: "" });
    return { ok: true, idle: true };
  }
  if (!(await stillEnabled(key))) return { ok: false, idle: true, reason: "off" };

  const snapshot = await collectSnapshot(requestId, trigger);
  if (!(await stillEnabled(key))) return { ok: false, idle: true, reason: "off" };
  const body = JSON.stringify(snapshot);
  const bodyHash = await sha256Hex(body);
  const snapshotNonce = randomNonce();
  const snapshotAuth = await hmacHex(
    key,
    protocolMessage("snapshot", browser, extensionId, snapshotNonce, requestId, bodyHash)
  );
  let submitted;
  try {
    submitted = await fetch(`${RECEIVER}/v1/snapshot`, {
    method: "POST",
    cache: "no-store",
    signal,
    headers: {
      ...signedHeaders(browser, extensionId, snapshotNonce, snapshotAuth),
      "content-type": "application/json"
    },
    body
    });
  } catch (error) {
    if (signal.aborted) return { ok: false, idle: true, reason: "off" };
    throw error;
  }
  const result = await readJson(submitted);
  if (!submitted.ok) throw new Error(result.error || `Capture failed (${submitted.status})`);
  const accepted = await verifyHmac(
    key,
    protocolMessage("accepted", browser, extensionId, snapshotNonce, requestId, result.captureId || ""),
    result.serverProof
  );
  if (!accepted) throw new Error("Receiver acceptance proof failed.");

  await chrome.storage.local.set({
    [KEYS.lastCaptureAt]: snapshot.capturedAt,
    [KEYS.lastError]: ""
  });
  await flashBadge("OK", "#147d64");
  return { ok: true, captured: true, tabCount: snapshot.tabs.length };
}

async function collectSnapshot(requestId, trigger) {
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

async function executeExactDuplicateMutation(command, key, browser, extensionId, signal) {
  const requestId = String(command.requestId || "");
  const targetsHash = String(command.targetsHash || "");
  const targets = Array.isArray(command.targets) ? command.targets : [];
  if (!/^[a-f0-9]{64}$/.test(targetsHash) || !requestId || targets.length > 1000) {
    throw new Error("Receiver mutation plan is invalid.");
  }
  const actualTargetsHash = await sha256Hex(JSON.stringify(targets));
  if (actualTargetsHash !== targetsHash) throw new Error("Receiver mutation plan hash failed.");

  const results = [];
  for (const target of targets) {
    const tabId = Number(target?.targetTabId);
    const keeperTabId = Number(target?.keeperTabId);
    const expectedUrlHash = String(target?.expectedUrlHash || "");
    if (!Number.isInteger(tabId) || !Number.isInteger(keeperTabId) || !/^[a-f0-9]{64}$/.test(expectedUrlHash)) {
      throw new Error("Receiver mutation target is invalid.");
    }
    if (signal.aborted || !(await stillEnabled(key))) {
      results.push({ tabId, status: "skipped", reason: "off" });
      continue;
    }
    let tab;
    let keeper;
    try {
      [tab, keeper] = await Promise.all([chrome.tabs.get(tabId), chrome.tabs.get(keeperTabId)]);
    } catch (_error) {
      results.push({ tabId, status: "skipped", reason: "tab_or_keeper_missing" });
      continue;
    }
    const targetUrl = String(tab.url || tab.pendingUrl || "");
    const keeperUrl = String(keeper.url || keeper.pendingUrl || "");
    const [targetUrlHash, keeperUrlHash] = await Promise.all([
      sha256Hex(targetUrl),
      sha256Hex(keeperUrl)
    ]);
    const validationReason = duplicateTargetReason(
      target,
      tab,
      keeper,
      targetUrlHash,
      keeperUrlHash
    );
    if (validationReason) {
      results.push({ tabId, status: "skipped", reason: validationReason });
      continue;
    }
    if (signal.aborted || !(await stillEnabled(key))) {
      results.push({ tabId, status: "skipped", reason: "off" });
      continue;
    }
    try {
      await chrome.tabs.remove(tabId);
      results.push({ tabId, status: "closed", reason: "exact_duplicate" });
    } catch (_error) {
      results.push({ tabId, status: "skipped", reason: "close_failed" });
    }
  }

  const bodyValue = {
    requestId,
    targetsHash,
    browser,
    extensionId,
    results
  };
  const body = JSON.stringify(bodyValue);
  const bodyHash = await sha256Hex(body);
  const resultNonce = randomNonce();
  const resultAuth = await hmacHex(
    key,
    protocolMessage("mutation", browser, extensionId, resultNonce, requestId, targetsHash, bodyHash)
  );
  const submitted = await fetch(`${RECEIVER}/v1/mutation`, {
    method: "POST",
    cache: "no-store",
    signal,
    headers: {
      ...signedHeaders(browser, extensionId, resultNonce, resultAuth),
      "content-type": "application/json"
    },
    body
  });
  const accepted = await readJson(submitted);
  if (!submitted.ok) throw new Error(accepted.error || `Mutation audit failed (${submitted.status}).`);
  const closedCount = results.filter(item => item.status === "closed").length;
  const skippedCount = results.length - closedCount;
  const receiverAccepted = await verifyHmac(
    key,
    protocolMessage(
      "mutation-accepted",
      browser,
      extensionId,
      resultNonce,
      requestId,
      targetsHash,
      closedCount,
      skippedCount
    ),
    accepted.serverProof
  );
  if (!receiverAccepted) throw new Error("Receiver mutation acceptance proof failed.");
  await chrome.storage.local.set({ [KEYS.lastError]: "" });
  await flashBadge("OK", "#147d64");
  return { ok: true, mutated: true, closedCount, skippedCount };
}

async function executeArchiveMutation(command, key, browser, extensionId, signal) {
  const requestId = String(command.requestId || "");
  const targetsHash = String(command.targetsHash || "");
  const targets = Array.isArray(command.targets) ? command.targets : [];
  if (!/^[a-f0-9]{64}$/.test(targetsHash) || !requestId || !targets.length || targets.length > 5000) {
    throw new Error("Receiver archive plan is invalid.");
  }
  const actualTargetsHash = await sha256Hex(JSON.stringify(targets));
  if (actualTargetsHash !== targetsHash) throw new Error("Receiver archive plan hash failed.");

  const firstWindowId = Number(targets[0]?.windowId);
  if (!Number.isInteger(firstWindowId)) throw new Error("Archive plan has no stable control window.");
  const controlTab = await chrome.tabs.create({
    windowId: firstWindowId,
    url: chrome.runtime.getURL("archive_complete.html"),
    active: false,
    pinned: true
  });
  if (!Number.isInteger(controlTab.id) || !Number.isInteger(controlTab.windowId)) {
    throw new Error("Could not create the archive verification tab.");
  }
  try {
    await chrome.storage.local.set({
      [KEYS.archiveControl]: {
        tabId: controlTab.id,
        windowId: controlTab.windowId,
        requestId
      }
    });
  } catch (error) {
    await chrome.tabs.remove(controlTab.id).catch(() => {});
    throw error;
  }

  const targetIds = new Set(targets.map(target => Number(target?.targetTabId)));
  const changedTargets = new Set();
  const onTargetUpdated = (tabId, changeInfo) => {
    if (
      targetIds.has(tabId)
      && (typeof changeInfo.url === "string" || changeInfo.status === "loading")
    ) {
      changedTargets.add(tabId);
    }
  };
  chrome.tabs.onUpdated.addListener(onTargetUpdated);
  const results = [];
  let retainControl = false;
  try {
    for (const target of targets) {
      const tabId = Number(target?.targetTabId);
      const expectedUrlHash = String(target?.expectedUrlHash || "");
      if (!Number.isInteger(tabId) || !/^[a-f0-9]{64}$/.test(expectedUrlHash)) {
        throw new Error("Receiver archive target is invalid.");
      }
      if (tabId === controlTab.id) {
        results.push({ tabId, status: "skipped", reason: "control_tab" });
        continue;
      }
      if (signal.aborted || !(await stillEnabled(key))) {
        results.push({ tabId, status: "skipped", reason: "off" });
        continue;
      }
      let tab;
      try {
        tab = await chrome.tabs.get(tabId);
      } catch (_error) {
        results.push({ tabId, status: "skipped", reason: "tab_missing" });
        continue;
      }
      const targetUrl = String(tab.url || tab.pendingUrl || "");
      const validationReason = archiveTargetReason(target, tab, await sha256Hex(targetUrl));
      if (validationReason || changedTargets.has(tabId)) {
        results.push({
          tabId,
          status: "skipped",
          reason: validationReason || "navigation_changed"
        });
        continue;
      }
      if (signal.aborted || !(await stillEnabled(key))) {
        results.push({ tabId, status: "skipped", reason: "off" });
        continue;
      }
      let finalTab;
      try {
        finalTab = await chrome.tabs.get(tabId);
      } catch (_error) {
        results.push({ tabId, status: "skipped", reason: "tab_missing" });
        continue;
      }
      const finalUrl = String(finalTab.url || finalTab.pendingUrl || "");
      const finalReason = archiveTargetReason(target, finalTab, await sha256Hex(finalUrl));
      if (finalReason || changedTargets.has(tabId)) {
        results.push({
          tabId,
          status: "skipped",
          reason: finalReason || "navigation_changed"
        });
        continue;
      }
      try {
        await chrome.tabs.remove(tabId);
        results.push({ tabId, status: "closed", reason: "captured_and_archived" });
      } catch (_error) {
        results.push({ tabId, status: "skipped", reason: "close_failed" });
      }
    }

    const bodyValue = {
      requestId,
      targetsHash,
      browser,
      extensionId,
      controlTabId: controlTab.id,
      controlWindowId: controlTab.windowId,
      results
    };
    const body = JSON.stringify(bodyValue);
    const bodyHash = await sha256Hex(body);
    const resultNonce = randomNonce();
    const resultAuth = await hmacHex(
      key,
      protocolMessage("mutation", browser, extensionId, resultNonce, requestId, targetsHash, bodyHash)
    );
    const submitted = await fetch(`${RECEIVER}/v1/mutation`, {
      method: "POST",
      cache: "no-store",
      signal,
      headers: {
        ...signedHeaders(browser, extensionId, resultNonce, resultAuth),
        "content-type": "application/json"
      },
      body
    });
    const accepted = await readJson(submitted);
    if (!submitted.ok) throw new Error(accepted.error || `Archive audit failed (${submitted.status}).`);
    const closedCount = results.filter(item => item.status === "closed").length;
    const skippedCount = results.length - closedCount;
    const receiverAccepted = await verifyHmac(
      key,
      protocolMessage(
        "mutation-accepted",
        browser,
        extensionId,
        resultNonce,
        requestId,
        targetsHash,
        closedCount,
        skippedCount
      ),
      accepted.serverProof
    );
    if (!receiverAccepted) throw new Error("Receiver archive acceptance proof failed.");
    retainControl = true;
    await chrome.storage.local.set({ [KEYS.lastError]: "" });
    await flashBadge("OK", "#147d64");
    return { ok: true, archived: true, closedCount, skippedCount };
  } finally {
    chrome.tabs.onUpdated.removeListener(onTargetUpdated);
    if (!retainControl) {
      await removeArchiveControl(controlTab.id, controlTab.windowId).catch(() => {});
    }
  }
}

async function executeArchiveControlCleanup(command, key, browser, extensionId, signal) {
  const requestId = String(command.requestId || "");
  const targetsHash = String(command.targetsHash || "");
  const targets = Array.isArray(command.targets) ? command.targets : [];
  if (!/^[a-f0-9]{64}$/.test(targetsHash) || !requestId || targets.length !== 1) {
    throw new Error("Receiver archive cleanup plan is invalid.");
  }
  if (await sha256Hex(JSON.stringify(targets)) !== targetsHash) {
    throw new Error("Receiver archive cleanup hash failed.");
  }
  const target = targets[0];
  const controlTabId = Number(target?.controlTabId);
  const controlWindowId = Number(target?.controlWindowId);
  const expectedUrlHash = String(target?.expectedUrlHash || "");
  if (
    !Number.isInteger(controlTabId)
    || !Number.isInteger(controlWindowId)
    || !/^[a-f0-9]{64}$/.test(expectedUrlHash)
  ) {
    throw new Error("Archive cleanup target is invalid.");
  }
  const controlTab = await chrome.tabs.get(controlTabId).catch(() => null);
  const controlUrl = String(controlTab?.url || controlTab?.pendingUrl || "");
  const validationReason = archiveControlReason(
    target,
    controlTab,
    await sha256Hex(controlUrl)
  );
  if (validationReason) throw new Error(`Archive cleanup refused: ${validationReason}.`);
  if (signal.aborted || !(await stillEnabled(key))) {
    return { ok: false, idle: true, reason: "off" };
  }

  let status = "closed";
  let reason = "removed";
  try {
    await chrome.tabs.remove(controlTabId);
    const remaining = await chrome.tabs.get(controlTabId).catch(() => null);
    if (remaining) {
      status = "skipped";
      reason = "removal_unverified";
    }
  } catch (_error) {
    const remaining = await chrome.tabs.get(controlTabId).catch(() => null);
    if (remaining) {
      status = "skipped";
      reason = "close_failed";
    } else {
      reason = "already_absent";
    }
  }
  if (status === "closed") await clearArchiveControl(controlTabId);

  const bodyValue = {
    requestId,
    targetsHash,
    browser,
    extensionId,
    controlTabId,
    controlWindowId,
    status,
    reason
  };
  const body = JSON.stringify(bodyValue);
  const bodyHash = await sha256Hex(body);
  const cleanupNonce = randomNonce();
  const cleanupAuth = await hmacHex(
    key,
    protocolMessage("cleanup", browser, extensionId, cleanupNonce, requestId, targetsHash, bodyHash)
  );
  const submitted = await fetch(`${RECEIVER}/v1/cleanup`, {
    method: "POST",
    cache: "no-store",
    signal,
    headers: {
      ...signedHeaders(browser, extensionId, cleanupNonce, cleanupAuth),
      "content-type": "application/json"
    },
    body
  });
  const accepted = await readJson(submitted);
  if (!submitted.ok) throw new Error(accepted.error || `Archive cleanup failed (${submitted.status}).`);
  const receiverAccepted = await verifyHmac(
    key,
    protocolMessage(
      "cleanup-accepted",
      browser,
      extensionId,
      cleanupNonce,
      requestId,
      targetsHash,
      controlTabId,
      controlWindowId,
      status,
      reason
    ),
    accepted.serverProof
  );
  if (!receiverAccepted) throw new Error("Receiver archive cleanup proof failed.");
  return { ok: status === "closed", cleaned: status === "closed", status, reason };
}

async function clearArchiveControl(tabId) {
  const stored = await chrome.storage.local.get([KEYS.archiveControl]);
  if (Number(stored[KEYS.archiveControl]?.tabId) === Number(tabId)) {
    await chrome.storage.local.remove([KEYS.archiveControl]);
  }
}

async function removeArchiveControl(tabId, windowId) {
  const tab = await chrome.tabs.get(tabId).catch(() => null);
  const expectedUrl = chrome.runtime.getURL("archive_complete.html");
  const actualUrl = String(tab?.url || tab?.pendingUrl || "");
  if (tab && Number(tab.windowId) === Number(windowId) && actualUrl === expectedUrl) {
    await chrome.tabs.remove(tabId).catch(() => {});
  }
  await clearArchiveControl(tabId);
}

async function cleanupStaleArchiveControl() {
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

function signedHeaders(browser, extensionId, nonce, auth) {
  return {
    "x-tabatlas-browser": browser,
    "x-tabatlas-extension": extensionId,
    "x-tabatlas-nonce": nonce,
    "x-tabatlas-auth": auth,
    "x-tabatlas-protocol": String(PROTOCOL_VERSION)
  };
}

async function stillEnabled(key) {
  const stored = await chrome.storage.local.get([KEYS.mode, KEYS.key]);
  return stored[KEYS.mode] === "on" && stored[KEYS.key] === key;
}

async function updateBadge() {
  const stored = await chrome.storage.local.get([KEYS.mode]);
  const on = stored[KEYS.mode] === "on";
  await chrome.action.setBadgeBackgroundColor({ color: on ? "#147d64" : "#667085" });
  await chrome.action.setBadgeText({ text: on ? "ON" : "" });
}

async function flashBadge(text, color) {
  await chrome.action.setBadgeBackgroundColor({ color });
  await chrome.action.setBadgeText({ text });
  setTimeout(() => updateBadge().catch(() => {}), 2500);
}

async function recordError(error) {
  await chrome.storage.local.set({ [KEYS.lastError]: safeError(error) });
  await flashBadge("!", "#b42318").catch(() => {});
}

async function readJson(response) {
  return response.json().catch(() => ({}));
}

function safeError(error) {
  return error instanceof Error ? error.message.slice(0, 240) : String(error).slice(0, 240);
}

function inferBrowser() {
  const ua = navigator.userAgent;
  if (ua.includes("Edg/")) return "edge";
  if (ua.includes("Chrome/")) return "chrome";
  return "chromium";
}
