import {
  hmacHex,
  keyFromToken,
  protocolMessage,
  randomNonce,
  verifyHmac
} from "../protocol.js";
import { KEYS, RECEIVER } from "./config.js";
import { synchronizeAlarm } from "./lifecycle.js";
import { inferBrowser, readJson } from "./platform.js";

export async function pair(code) {
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
