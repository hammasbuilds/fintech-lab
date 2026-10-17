// Drive a real browser over the DevTools Protocol, with no dependencies.
//
// A dashboard here is a static page the server hands over untouched: Python never parses
// it, so a reference error leaves a blank panel and every test stays green. `node --check`
// catches a syntax error and nothing catches "the renderer read a field that is not there"
// or "the button is wired to a selector that does not exist".
//
// So this opens the page in headless Chrome, clicks what a reader would click, waits for
// the thing that should appear, and fails loudly when it does not. Node 22+ has a global
// WebSocket, and Chrome ships the protocol, so the whole client is the file you are
// reading.
//
//   node scripts/ui_smoke.mjs <chrome-path> <plan.json>
//
// The plan is {"url": ..., "steps": [...]}, where a step is one of:
//   {"waitFor": "#sel"}                wait for the selector to exist
//   {"click": "#sel"}                  click it
//   {"select": "#sel", "value": "x"}   set a <select> and fire change
//   {"expectText": "#sel", "contains": "..."}   assert the element's text
//   {"expectNoEmpty": ["#a", "#b"]}    assert each element has rendered something
//
// Console errors and page exceptions fail the run whatever else happens, because a panel
// that throws halfway through still looks finished.

import { spawn } from "node:child_process";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

const [chromePath, planPath] = process.argv.slice(2);
if (!chromePath || !planPath) {
  console.error("usage: node scripts/ui_smoke.mjs <chrome> <plan.json>");
  process.exit(2);
}

const plan = JSON.parse(await readFile(planPath, "utf8"));
const port = 9222 + Math.floor(Math.random() * 500);
const profile = await mkdtemp(join(tmpdir(), "fintech-ui-"));

const chrome = spawn(
  chromePath,
  [
    "--headless=new",
    "--disable-gpu",
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-extensions",
    "--disable-background-networking",
    "--window-size=1280,1600",
    `--user-data-dir=${profile}`,
    `--remote-debugging-port=${port}`,
    "about:blank",
  ],
  { stdio: ["ignore", "ignore", "pipe"] },
);
let chromeStderr = "";
chrome.stderr.on("data", (chunk) => {
  chromeStderr += String(chunk);
});

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function endpoint() {
  for (let attempt = 0; attempt < 60; attempt++) {
    try {
      const reply = await fetch(`http://127.0.0.1:${port}/json/version`);
      const body = await reply.json();
      if (body.webSocketDebuggerUrl) return body.webSocketDebuggerUrl;
    } catch {
      // Chrome is still starting.
    }
    await sleep(250);
  }
  throw new Error(`Chrome never opened a debugging port.\n${chromeStderr.slice(-800)}`);
}

class Session {
  constructor(socket) {
    this.socket = socket;
    this.id = 0;
    this.pending = new Map();
    this.sessionId = null;
    this.problems = [];
    socket.addEventListener("message", (event) => this.receive(JSON.parse(event.data)));
  }

  receive(message) {
    if (message.id && this.pending.has(message.id)) {
      const { resolve, reject } = this.pending.get(message.id);
      this.pending.delete(message.id);
      if (message.error) reject(new Error(message.error.message));
      else resolve(message.result);
      return;
    }
    if (message.method === "Runtime.exceptionThrown") {
      const details = message.params.exceptionDetails;
      this.problems.push(
        `page threw: ${details.exception?.description || details.text}`.split("\n")[0],
      );
    }
    if (message.method === "Runtime.consoleAPICalled" && message.params.type === "error") {
      const text = message.params.args.map((arg) => arg.value ?? arg.description).join(" ");
      this.problems.push(`console error: ${text}`);
    }
  }

  send(method, params = {}) {
    const id = ++this.id;
    const payload = { id, method, params };
    if (this.sessionId) payload.sessionId = this.sessionId;
    this.socket.send(JSON.stringify(payload));
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
      setTimeout(() => {
        if (this.pending.has(id)) {
          this.pending.delete(id);
          reject(new Error(`${method} timed out`));
        }
      }, 60_000);
    });
  }

  async evaluate(expression) {
    const result = await this.send("Runtime.evaluate", {
      expression,
      awaitPromise: true,
      returnByValue: true,
    });
    if (result.exceptionDetails) {
      throw new Error(result.exceptionDetails.exception?.description || "evaluate failed");
    }
    return result.result.value;
  }
}

const socketUrl = await endpoint();
const socket = new WebSocket(socketUrl);
await new Promise((resolve, reject) => {
  socket.addEventListener("open", resolve);
  socket.addEventListener("error", () => reject(new Error("could not attach to Chrome")));
});
const session = new Session(socket);

const { targetId } = await session.send("Target.createTarget", { url: "about:blank" });
const attached = await session.send("Target.attachToTarget", { targetId, flatten: true });
session.sessionId = attached.sessionId;
await session.send("Runtime.enable");
await session.send("Page.enable");

const failures = [];
const log = [];

try {
  await session.send("Page.navigate", { url: plan.url });
  await session.evaluate(
    `new Promise((done) => {
      if (document.readyState === "complete") return done(true);
      window.addEventListener("load", () => done(true), { once: true });
    })`,
  );
  // The boot function is async, so give it a moment to finish its first fetches.
  await sleep(plan.settle ?? 1200);

  for (const step of plan.steps) {
    if (step.waitFor) {
      const ok = await session.evaluate(
        `new Promise((done) => {
          const started = Date.now();
          const tick = () => {
            const node = document.querySelector(${JSON.stringify(step.waitFor)});
            if (node && (node.children.length || (node.textContent || "").trim())) return done(true);
            if (Date.now() - started > ${step.timeout ?? 30000}) return done(false);
            setTimeout(tick, 150);
          };
          tick();
        })`,
      );
      if (!ok) failures.push(`nothing rendered into ${step.waitFor}`);
      else log.push(`rendered ${step.waitFor}`);
      continue;
    }
    if (step.click) {
      const ok = await session.evaluate(
        `(() => {
          const node = document.querySelector(${JSON.stringify(step.click)});
          if (!node) return false;
          node.click();
          return true;
        })()`,
      );
      if (!ok) failures.push(`no element to click at ${step.click}`);
      else log.push(`clicked ${step.click}`);
      await sleep(step.after ?? 250);
      continue;
    }
    if (step.select) {
      const ok = await session.evaluate(
        `(() => {
          const node = document.querySelector(${JSON.stringify(step.select)});
          if (!node) return false;
          node.value = ${JSON.stringify(step.value)};
          node.dispatchEvent(new Event("change", { bubbles: true }));
          return node.value === ${JSON.stringify(step.value)};
        })()`,
      );
      if (!ok) failures.push(`could not set ${step.select} to ${step.value}`);
      else log.push(`set ${step.select} = ${step.value}`);
      continue;
    }
    if (step.expectText) {
      const text = await session.evaluate(
        `(document.querySelector(${JSON.stringify(step.expectText)}) || {}).textContent || ""`,
      );
      if (!String(text).includes(step.contains)) {
        failures.push(
          `${step.expectText} does not contain ${JSON.stringify(step.contains)}; it has ` +
            JSON.stringify(String(text).trim().slice(0, 160)),
        );
      } else {
        log.push(`${step.expectText} contains ${JSON.stringify(step.contains)}`);
      }
      continue;
    }
    if (step.expectNoEmpty) {
      for (const selector of step.expectNoEmpty) {
        const filled = await session.evaluate(
          `(() => {
            const node = document.querySelector(${JSON.stringify(selector)});
            if (!node) return "missing";
            return (node.children.length || (node.textContent || "").trim()) ? "filled" : "empty";
          })()`,
        );
        if (filled !== "filled") failures.push(`${selector} is ${filled}`);
        else log.push(`${selector} is filled`);
      }
    }
  }
} catch (error) {
  failures.push(String(error.message || error));
} finally {
  socket.close();
  chrome.kill();
  await rm(profile, { recursive: true, force: true }).catch(() => {});
}

const problems = [...new Set(session.problems)];
console.log(JSON.stringify({ ok: !failures.length && !problems.length, log, failures, problems }, null, 2));
process.exit(failures.length || problems.length ? 1 : 0);
