import {
  archiveControlReason,
  hmacHex,
  protocolMessage,
  randomNonce,
  sha256Hex,
  verifyHmac
} from "../protocol.js";
import { clearArchiveControl } from "./archive_control.js";
import { RECEIVER } from "./config.js";
import { readJson, signedHeaders, stillEnabled } from "./platform.js";

export async function executeArchiveControlCleanup(command, key, browser, extensionId, signal) {
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

  const windowTabs = await chrome.tabs.query({ windowId: controlWindowId });
  let handoffTab = null;
  if (!windowTabs.some(tab => Number(tab.id) !== controlTabId)) {
    handoffTab = await chrome.tabs.create({
      windowId: controlWindowId,
      url: "about:blank",
      active: false,
      pinned: false
    });
    if (!Number.isInteger(handoffTab.id) || Number(handoffTab.windowId) !== controlWindowId) {
      if (Number.isInteger(handoffTab?.id)) {
        await chrome.tabs.remove(handoffTab.id).catch(() => {});
      }
      throw new Error("Could not create the archive cleanup handoff tab.");
    }
  }

  let status = "closed";
  let reason = handoffTab ? "removed_with_handoff" : "removed";
  try {
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
    if (status === "closed") await clearArchiveControl(controlTabId);
    return { ok: status === "closed", cleaned: status === "closed", status, reason };
  } finally {
    if (Number.isInteger(handoffTab?.id)) {
      await chrome.tabs.remove(handoffTab.id).catch(() => {});
    }
  }
}
