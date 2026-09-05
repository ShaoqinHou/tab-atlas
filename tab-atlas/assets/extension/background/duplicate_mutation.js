import {
  duplicateTargetReason,
  hmacHex,
  protocolMessage,
  randomNonce,
  sha256Hex,
  verifyHmac
} from "../protocol.js";
import { KEYS, RECEIVER } from "./config.js";
import { flashBadge, readJson, signedHeaders, stillEnabled } from "./platform.js";

export async function executeExactDuplicateMutation(command, key, browser, extensionId, signal) {
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
