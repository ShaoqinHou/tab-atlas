import {
  archiveTargetReason,
  hmacHex,
  protocolMessage,
  randomNonce,
  sha256Hex,
  verifyHmac
} from "../protocol.js";
import { removeArchiveControl } from "./archive_control.js";
import { KEYS, RECEIVER } from "./config.js";
import { flashBadge, readJson, signedHeaders, stillEnabled } from "./platform.js";

export async function executeArchiveMutation(command, key, browser, extensionId, signal) {
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
