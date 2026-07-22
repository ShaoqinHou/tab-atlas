import { POLL_ALARM } from "./background/config.js";
import { initialize } from "./background/lifecycle.js";
import { handleMessage } from "./background/messages.js";
import { pollReceiver } from "./background/polling.js";
import { recordError, safeError } from "./background/platform.js";

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
