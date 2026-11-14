// 临时基准驱动：Vite + Edge Headless CDP，比较 xterm 默认渲染器与 WebGL。
import { spawn } from "node:child_process";
import { rm } from "node:fs/promises";
import { setTimeout as sleep } from "node:timers/promises";

const FRONTEND = process.cwd();
const PORT = 1420;
const DEBUG_PORT = 9333;
const EDGE = "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe";
const PROFILE = `${FRONTEND}/.bench-profile`;

class Cdp {
  constructor(url) {
    this.ws = new WebSocket(url);
    this.nextId = 0;
    this.pending = new Map();
    this.ws.addEventListener("message", (event) => {
      const message = JSON.parse(event.data);
      const entry = this.pending.get(message.id);
      if (!entry) return;
      this.pending.delete(message.id);
      if (message.error) entry.reject(new Error(JSON.stringify(message.error)));
      else entry.resolve(message.result);
    });
  }

  ready() {
    return new Promise((resolve, reject) => {
      this.ws.addEventListener("open", resolve, { once: true });
      this.ws.addEventListener("error", reject, { once: true });
    });
  }

  send(method, params = {}) {
    const id = ++this.nextId;
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
      this.ws.send(JSON.stringify({ id, method, params }));
    });
  }

  close() {
    this.ws.close();
  }
}

async function waitHttp(url, timeoutMs = 30000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      const response = await fetch(url);
      if (response.ok) return;
    } catch {
      // retry
    }
    await sleep(200);
  }
  throw new Error(`timeout waiting for ${url}`);
}

async function runOnce(mode) {
  const targetUrl =
    `http://127.0.0.1:${PORT}/bench/terminal-bench.html?renderer=${mode.renderer}` +
    (mode.batch ? `&batch=${mode.batch}` : "");
  const created = await fetch(
    `http://127.0.0.1:${DEBUG_PORT}/json/new?${encodeURIComponent(targetUrl)}`,
    { method: "PUT" },
  ).then((response) => response.json());
  const cdp = new Cdp(created.webSocketDebuggerUrl);
  await cdp.ready();
  try {
    await cdp.send("Runtime.enable");
    await cdp.send("Performance.enable");
    const baseline = await cdp.send("Performance.getMetrics");
    const deadline = Date.now() + 120000;
    let result = null;
    while (Date.now() < deadline) {
      const evaluated = await cdp.send("Runtime.evaluate", {
        expression: "window.__benchResult ? JSON.stringify(window.__benchResult) : null",
        returnByValue: true,
      });
      const value = evaluated.result?.value;
      if (typeof value === "string") {
        result = JSON.parse(value);
        break;
      }
      await sleep(250);
    }
    const final = await cdp.send("Performance.getMetrics");
    const metric = (snapshot, name) =>
      snapshot.metrics.find((item) => item.name === name)?.value ?? 0;
    const cpuSeconds = Number(
      (metric(final, "TaskDuration") - metric(baseline, "TaskDuration")).toFixed(3),
    );
    return result ? { ...result, cpuSeconds } : { renderer: mode.renderer, error: "timeout" };
  } finally {
    cdp.close();
    await fetch(`http://127.0.0.1:${DEBUG_PORT}/json/close/${created.id}`).catch(() => undefined);
  }
}

async function main() {
  await rm(PROFILE, { recursive: true, force: true });
  const vite = spawn("npx", ["vite", "--port", String(PORT), "--strictPort"], {
    cwd: FRONTEND,
    shell: true,
    stdio: "ignore",
  });
  const edge = spawn(
    EDGE,
    [
      "--headless=new",
      `--remote-debugging-port=${DEBUG_PORT}`,
      `--user-data-dir=${PROFILE}`,
      "--no-first-run",
      "--no-default-browser-check",
      "--window-size=1400,900",
      "about:blank",
    ],
    { stdio: "ignore" },
  );
  try {
    await waitHttp(`http://127.0.0.1:${PORT}/bench/terminal-bench.html`);
    await waitHttp(`http://127.0.0.1:${DEBUG_PORT}/json/version`);
    const results = [];
    for (const mode of [
      { renderer: "default" },
      { renderer: "webgl" },
      { renderer: "default", batch: 24 },
      { renderer: "webgl", batch: 24 },
    ]) {
      results.push(await runOnce(mode));
    }
    console.log(JSON.stringify(results, null, 2));
  } finally {
    edge.kill();
    vite.kill();
    await sleep(500);
    await rm(PROFILE, { recursive: true, force: true });
  }
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
