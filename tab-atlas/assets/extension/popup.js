const elements = {
  browser: document.getElementById("browser"),
  mode: document.getElementById("mode"),
  modeLabel: document.getElementById("modeLabel"),
  pairing: document.getElementById("pairing"),
  lastCapture: document.getElementById("lastCapture"),
  pairPanel: document.getElementById("pairPanel"),
  code: document.getElementById("code"),
  pair: document.getElementById("pair"),
  capture: document.getElementById("capture"),
  message: document.getElementById("message")
};

elements.mode.addEventListener("change", () => run(async () => {
  const statusValue = await send({ type: "tabatlas:set-mode", mode: elements.mode.checked ? "on" : "off" });
  if (!statusValue.ok) throw new Error(statusValue.error || "Mode change failed.");
  render(statusValue);
  say(statusValue.mode === "on" ? "Passive capture is on." : "Capture is fully off.");
}));

elements.pair.addEventListener("click", () => run(async () => {
  const code = elements.code.value.trim().toUpperCase();
  const result = await send({ type: "tabatlas:pair", code });
  if (!result.ok) throw new Error(result.error || "Pairing failed.");
  elements.code.value = "";
  say("Paired. Passive capture is on.");
  render(await status());
}));

elements.capture.addEventListener("click", () => run(async () => {
  say("Checking for a capture request...");
  const result = await send({ type: "tabatlas:capture-now" });
  if (!result.ok && !result.idle) throw new Error(result.error || "Capture failed.");
  say(result.captured ? `Captured ${result.tabCount} tabs.` : "No receiver is requesting a capture.");
  render(await status());
}));

run(async () => render(await status()));

async function status() {
  return send({ type: "tabatlas:status" });
}

async function send(message) {
  const response = await chrome.runtime.sendMessage(message);
  if (!response) throw new Error("The extension service worker did not respond.");
  return response;
}

async function run(fn) {
  setBusy(true);
  try {
    elements.message.className = "message";
    await fn();
  } catch (error) {
    elements.message.className = "message error";
    elements.message.textContent = error instanceof Error ? error.message : String(error);
  } finally {
    setBusy(false);
    render(await status().catch(() => ({ mode: "off", paired: false, browser: "browser" })));
  }
}

function render(value) {
  const mode = value.mode === "on";
  elements.browser.textContent = title(value.browser || "browser");
  elements.mode.checked = mode;
  elements.modeLabel.textContent = mode ? "ON" : "OFF";
  elements.pairing.textContent = value.paired ? "Paired" : "Unpaired";
  elements.lastCapture.textContent = value.lastCaptureAt ? new Date(value.lastCaptureAt).toLocaleString() : "Never";
  elements.pairPanel.hidden = Boolean(value.paired);
  elements.mode.disabled = !value.paired;
  elements.capture.disabled = !mode || !value.paired;
  if (value.lastError && !elements.message.textContent) {
    elements.message.className = "message error";
    elements.message.textContent = value.lastError;
  }
}

function say(text) {
  elements.message.className = "message";
  elements.message.textContent = text;
}

function setBusy(busy) {
  elements.pair.disabled = busy;
  elements.mode.disabled = busy;
  if (busy) elements.capture.disabled = true;
}

function title(value) {
  return value.charAt(0).toUpperCase() + value.slice(1);
}
