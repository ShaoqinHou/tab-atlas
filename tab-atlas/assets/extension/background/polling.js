import {
  hmacHex,
  protocolMessage,
  randomNonce,
  sha256Hex,
  verifyHmac
} from "../protocol.js";
import { executeArchiveControlCleanup } from "./archive_cleanup.js";
import { executeArchiveMutation } from "./archive_mutation.js";
import { collectSnapshot } from "./capture.js";
import { KEYS, RECEIVER } from "./config.js";
import { executeExactDuplicateMutation } from "./duplicate_mutation.js";
import { synchronizeAlarm } from "./lifecycle.js";
import {
  flashBadge,
  inferBrowser,
  readJson,
  signedHeaders,
  stillEnabled
} from "./platform.js";

let pollInFlight = null;
let pollAbortController = null;

export function abortPolling() {
  if (pollAbortController) pollAbortController.abort();
}

export async function pollReceiver(trigger) {
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
