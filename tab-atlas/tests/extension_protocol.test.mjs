import assert from "node:assert/strict";
import { createHash, createHmac, webcrypto } from "node:crypto";
import test from "node:test";

globalThis.crypto ??= webcrypto;

const protocol = await import("../assets/extension/protocol.js");

test("extension proofs match an independent HMAC implementation", async () => {
  const token = "pairing-token-for-test";
  const key = await protocol.keyFromToken(token);
  const expectedKey = createHash("sha256").update(token).digest("hex");
  const message = protocol.protocolMessage("command", "chrome", "extension-id", "1".repeat(32));
  const expectedProof = createHmac("sha256", Buffer.from(expectedKey, "hex"))
    .update(message)
    .digest("hex");

  assert.equal(key, expectedKey);
  assert.equal(await protocol.hmacHex(key, message), expectedProof);
  assert.equal(await protocol.verifyHmac(key, message, expectedProof), true);
  assert.equal(await protocol.verifyHmac(key, `${message}x`, expectedProof), false);
});

test("exact duplicate validation protects live tab state and context", () => {
  const hash = "a".repeat(64);
  const plan = { expectedUrlHash: hash, windowId: "7", groupId: "-1" };
  const tab = { windowId: 7, groupId: -1, active: false, highlighted: false, pinned: false, audible: false };
  const keeper = { windowId: 7, groupId: -1 };

  assert.equal(protocol.duplicateTargetReason(plan, tab, keeper, hash, hash), "");
  assert.equal(protocol.duplicateTargetReason(plan, { ...tab, active: true }, keeper, hash, hash), "tab_became_protected");
  assert.equal(protocol.duplicateTargetReason(plan, { ...tab, highlighted: true }, keeper, hash, hash), "tab_became_protected");
  assert.equal(protocol.duplicateTargetReason(plan, tab, keeper, "b".repeat(64), hash), "url_changed");
  assert.equal(protocol.duplicateTargetReason(plan, { ...tab, groupId: 9 }, keeper, hash, hash), "context_changed");
});
