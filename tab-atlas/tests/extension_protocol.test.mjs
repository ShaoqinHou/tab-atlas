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
