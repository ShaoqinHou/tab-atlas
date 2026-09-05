import { getStatus, setMode } from "./lifecycle.js";
import { pair } from "./pairing.js";
import { abortPolling, pollReceiver } from "./polling.js";

export async function handleMessage(message) {
  switch (message.type) {
    case "tabatlas:status":
      return getStatus();
    case "tabatlas:set-mode":
      return setMode(message.mode === "on" ? "on" : "off", pollReceiver, abortPolling);
    case "tabatlas:pair":
      return pair(String(message.code || "").trim().toUpperCase());
    case "tabatlas:capture-now":
      return pollReceiver("popup");
    default:
      return { ok: false, error: "unsupported message" };
  }
}
