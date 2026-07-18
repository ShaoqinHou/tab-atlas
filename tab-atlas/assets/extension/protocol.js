const encoder = new TextEncoder();

export function protocolMessage(...parts) {
  return ["tabatlas-v1", ...parts.map(part => String(part))].join("\n");
}

export async function keyFromToken(token) {
  return sha256Hex(String(token));
}

export async function sha256Hex(value) {
  const digest = await crypto.subtle.digest("SHA-256", encoder.encode(String(value)));
  return bytesToHex(new Uint8Array(digest));
}

export async function hmacHex(keyHex, message) {
  const key = await crypto.subtle.importKey(
    "raw",
    hexToBytes(keyHex),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"]
  );
  const signature = await crypto.subtle.sign("HMAC", key, encoder.encode(message));
  return bytesToHex(new Uint8Array(signature));
}

export async function verifyHmac(keyHex, message, suppliedHex) {
  if (!/^[a-f0-9]{64}$/.test(String(suppliedHex || ""))) return false;
  const key = await crypto.subtle.importKey(
    "raw",
    hexToBytes(keyHex),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["verify"]
  );
  return crypto.subtle.verify(
    "HMAC",
    key,
    hexToBytes(suppliedHex),
    encoder.encode(message)
  );
}

export function randomNonce() {
  const bytes = new Uint8Array(16);
  crypto.getRandomValues(bytes);
  return bytesToHex(bytes);
}

function hexToBytes(value) {
  const text = String(value || "");
  if (!/^[a-f0-9]+$/.test(text) || text.length % 2 !== 0) {
    throw new Error("Invalid protocol key encoding.");
  }
  const bytes = new Uint8Array(text.length / 2);
  for (let index = 0; index < bytes.length; index += 1) {
    bytes[index] = Number.parseInt(text.slice(index * 2, index * 2 + 2), 16);
  }
  return bytes;
}

function bytesToHex(bytes) {
  return [...bytes].map(value => value.toString(16).padStart(2, "0")).join("");
}
