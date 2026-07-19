import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const { chromium } = loadPlaywright();
const EXTENSION = path.join(ROOT, "state", "extension");
const EXTENSION_ID = "ohgpplkophdikjnbefigdhikdooehmkh";
const PYTHON = process.env.PYTHON || "python";
const TARGETS = {
  chromium: { executablePath: chromium.executablePath(), protocolBrowser: "chrome" },
  chrome: {
    executablePath: "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
    protocolBrowser: "chrome"
  },
  edge: {
    executablePath: "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
    protocolBrowser: "edge"
  }
};

const requested = process.argv[2] || "all";
const browsers = requested === "all" ? ["chromium", "edge"] : [requested];
if (!browsers.every(browser => Object.hasOwn(TARGETS, browser))) {
  throw new Error("Usage: node tests/live_extension_e2e.mjs [all|chromium|chrome|edge]");
}
if (!fs.existsSync(path.join(EXTENSION, "manifest.json"))) {
  throw new Error(`Prepared extension is missing: ${EXTENSION}`);
}

const summaries = [];
for (const browser of browsers) summaries.push(await exerciseBrowser(browser));
process.stdout.write(`${JSON.stringify({ complete: true, browsers: summaries }, null, 2)}\n`);

async function exerciseBrowser(browser) {
  const { executablePath, protocolBrowser } = TARGETS[browser];
  if (!fs.existsSync(executablePath)) throw new Error(`${browser} executable is missing`);
  const isolatedRoot = path.join(ROOT, "state", "isolated-e2e", browser);
  assertInsideState(isolatedRoot);
  fs.rmSync(isolatedRoot, { recursive: true, force: true });
  const profileDir = path.join(isolatedRoot, "profile");
  const stateDir = path.join(isolatedRoot, "state");
  fs.mkdirSync(profileDir, { recursive: true });
  fs.mkdirSync(stateDir, { recursive: true });

  const context = await chromium.launchPersistentContext(profileDir, {
    executablePath,
    headless: true,
    ignoreDefaultArgs: ["--disable-extensions"],
    args: [
      "--headless=new",
      `--disable-extensions-except=${EXTENSION}`,
      `--load-extension=${EXTENSION}`,
      "--no-first-run",
      "--no-default-browser-check"
    ]
  });
  try {
    const popup = await context.newPage();
    await popup.goto(`chrome-extension://${EXTENSION_ID}/popup.html`, { timeout: 20_000 });
    let worker = context.serviceWorkers().find(value => value.url().includes(EXTENSION_ID));
    if (!worker) {
      worker = await context.waitForEvent("serviceworker", {
        predicate: value => value.url().includes(EXTENSION_ID),
        timeout: 20_000
      });
    }
    assert.match(worker.url(), new RegExp(`^chrome-extension://${EXTENSION_ID}/`));
    const pairing = startCli(stateDir, ["pair", "--browser", protocolBrowser, "--timeout", "60"]);
    const code = await pairing.waitFor(/One-time code:\s*([A-Z2-9]{8})/);
    await popup.locator("#code").fill(code[1]);
    await popup.locator("#pair").click();
    await popup.locator("#pairing").filter({ hasText: "Paired" }).waitFor({ timeout: 20_000 });
    await pairing.completed();
    await startCli(stateDir, [
      "dedupe-approval",
      "grant",
      "--scope",
      "Isolated E2E: close exact HTTPS duplicates in this disposable profile only."
    ]).completed();

    const duplicateUrl = "https://example.com/?tabatlas-e2e=duplicate";
    const uniqueUrl = "https://example.com/?tabatlas-e2e=unique";
    const keeper = await context.newPage();
    await keeper.goto(duplicateUrl, { waitUntil: "domcontentloaded" });
    const duplicateOne = await context.newPage();
    await duplicateOne.goto(duplicateUrl, { waitUntil: "domcontentloaded" });
    const duplicateTwo = await context.newPage();
    await duplicateTwo.goto(duplicateUrl, { waitUntil: "domcontentloaded" });
    const unique = await context.newPage();
    await unique.goto(uniqueUrl, { waitUntil: "domcontentloaded" });

    const capture = startCli(stateDir, ["capture", "--browser", protocolBrowser, "--timeout", "60"]);
    await pollReceiverFromPopup(popup, capture, 90_000);
    const captureResult = parseTrailingJson(await capture.completed());
    assert.equal(captureResult.complete, true);

    const dedupe = startCli(stateDir, [
      "dedupe",
      "--browser",
      protocolBrowser,
      "--execute",
      "--timeout",
      "60"
    ]);
    await pollReceiverFromPopup(popup, dedupe, 180_000);
    const dedupeResult = parseTrailingJson(await dedupe.completed());
    assert.equal(dedupeResult.complete, true);
    assert.equal(dedupeResult.closed, 2);
    assert.equal(dedupeResult.skipped, 0);
    assert.equal(dedupeResult.postVerifiedBrowsers, 1);

    const livePages = context.pages().filter(page => !page.isClosed());
    assert.equal(livePages.filter(page => page.url() === duplicateUrl).length, 1);
    assert.equal(livePages.filter(page => page.url() === uniqueUrl).length, 1);
    const audit = fs.readFileSync(dedupeResult.auditPath, "utf8");
    assert.equal(audit.includes("example.com"), false);
    assert.equal(audit.includes(duplicateUrl), false);

    return {
      browser,
      protocolBrowser,
      extensionId: EXTENSION_ID,
      capturedTabs: captureResult.captured[protocolBrowser].tabs,
      plannedClosures: dedupeResult.summary.plannedClosures,
      closed: dedupeResult.closed,
      skipped: dedupeResult.skipped,
      postCaptureVerified: dedupeResult.postVerifiedBrowsers === 1
    };
  } finally {
    await context.close();
  }
}

async function pollReceiverFromPopup(popup, child, timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  while (!child.exited()) {
    if (Date.now() > deadline) throw new Error(`Timed out polling receiver.\n${child.output()}`);
    await popup.bringToFront();
    const button = popup.locator("#capture");
    if (await button.isEnabled().catch(() => false)) {
      await button.click().catch(() => {});
    }
    await new Promise(resolve => setTimeout(resolve, 900));
  }
}

function startCli(stateDir, args) {
  const child = spawn(
    PYTHON,
    ["scripts/tab_atlas.py", "--state", stateDir, ...args],
    { cwd: ROOT, windowsHide: true, stdio: ["ignore", "pipe", "pipe"] }
  );
  let stdout = "";
  let stderr = "";
  let exit = null;
  const waiters = [];
  child.stdout.on("data", chunk => {
    stdout += chunk.toString();
    for (const waiter of waiters.splice(0)) waiter();
  });
  child.stderr.on("data", chunk => {
    stderr += chunk.toString();
    for (const waiter of waiters.splice(0)) waiter();
  });
  const completion = new Promise((resolve, reject) => {
    child.on("error", reject);
    child.on("exit", code => {
      exit = code;
      for (const waiter of waiters.splice(0)) waiter();
      if (code === 0) resolve(stdout);
      else reject(new Error(`CLI exited ${code}.\n${stdout}\n${stderr}`));
    });
  });
  return {
    exited: () => exit !== null,
    output: () => `${stdout}\n${stderr}`,
    completed: () => completion,
    waitFor: async regex => {
      const deadline = Date.now() + 20_000;
      while (Date.now() < deadline) {
        const match = stdout.match(regex);
        if (match) return match;
        if (exit !== null) throw new Error(`CLI exited before expected output.\n${stdout}\n${stderr}`);
        await Promise.race([
          new Promise(resolve => waiters.push(resolve)),
          new Promise(resolve => setTimeout(resolve, 200))
        ]);
      }
      throw new Error(`Timed out waiting for CLI output.\n${stdout}\n${stderr}`);
    }
  };
}

function parseTrailingJson(output) {
  const start = output.indexOf("{");
  if (start < 0) throw new Error(`CLI did not return JSON.\n${output}`);
  return JSON.parse(output.slice(start));
}

function assertInsideState(target) {
  const stateRoot = path.resolve(ROOT, "state");
  const relative = path.relative(stateRoot, path.resolve(target));
  if (!relative || relative.startsWith("..") || path.isAbsolute(relative)) {
    throw new Error(`Refusing to manage an E2E path outside state: ${target}`);
  }
}

function loadPlaywright() {
  try {
    return require("playwright");
  } catch (originalError) {
    const modulesRoot = process.env.CODEX_NODE_MODULES || path.join(
      process.env.USERPROFILE || "",
      ".cache",
      "codex-runtimes",
      "codex-primary-runtime",
      "dependencies",
      "node",
      "node_modules"
    );
    const pnpmRoot = path.join(modulesRoot, ".pnpm");
    if (!fs.existsSync(pnpmRoot)) throw originalError;
    const coreDirectory = fs.readdirSync(pnpmRoot)
      .filter(name => name.startsWith("playwright-core@"))
      .sort()
      .at(-1);
    if (!coreDirectory) throw originalError;
    return require(path.join(pnpmRoot, coreDirectory, "node_modules", "playwright-core"));
  }
}
