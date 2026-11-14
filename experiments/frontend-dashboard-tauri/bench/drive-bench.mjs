// 临时基准驱动：Vite + Edge Headless CDP，比较 xterm 默认渲染器与 WebGL。
import { spawn } from "node:child_process";
import { rm } from "node:fs/promises";
import os from "node:os";
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

function msedgePids(rootPid) {
  return new Promise((resolve) => {
    const child = spawn(
      "powershell",
      [
        "-NoProfile",
        "-Command",
        "Get-CimInstance Win32_Process -Filter \"name='msedge.exe'\" | Select-Object ProcessId,ParentProcessId | ConvertTo-Json -Compress",
      ],
      { stdio: ["ignore", "pipe", "ignore"] },
    );
    let text = "";
    child.stdout.on("data", (chunk) => {
      text += chunk.toString();
    });
    child.on("close", () => {
      let rows = [];
      try {
        const parsed = JSON.parse(text);
        rows = Array.isArray(parsed) ? parsed : [parsed];
      } catch {
        resolve([]);
        return;
      }
      const byParent = new Map();
      for (const row of rows) {
        const parent = Number(row.ParentProcessId);
        const list = byParent.get(parent) ?? [];
        list.push(Number(row.ProcessId));
        byParent.set(parent, list);
      }
      const result = [];
      const queue = [rootPid];
      while (queue.length) {
        const current = queue.shift();
        if (result.includes(current)) continue;
        result.push(current);
        queue.push(...(byParent.get(current) ?? []));
      }
      resolve(result);
    });
  });
}

function startGpuSampler(pids) {
  const pattern = pids.map((pid) => `pid_${pid}_`).join("|");
  const script =
    `$regex='${pattern}'; while($true){ ` +
    `$s=Get-Counter '\\GPU Engine(*)\\Utilization Percentage' -ErrorAction SilentlyContinue | ` +
    `Select-Object -ExpandProperty CounterSamples | Where-Object { $_.InstanceName -match $regex }; ` +
    `$sum=($s | Measure-Object -Property CookedValue -Sum).Sum; if($null -eq $sum){$sum=0}; ` +
    `[DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds().ToString() + ' ' + $sum; ` +
    `Start-Sleep -Milliseconds 400 }`;
  const child = spawn("powershell", ["-NoProfile", "-Command", script], {
    stdio: ["ignore", "pipe", "ignore"],
  });
  const samples = [];
  let buffer = "";
  child.stdout.on("data", (chunk) => {
    buffer += chunk.toString();
    const lines = buffer.split(/\r?\n/);
    buffer = lines.pop() ?? "";
    for (const line of lines) {
      const [timeText, valueText] = line.trim().split(/\s+/);
      const time = Number(timeText);
      const value = Number(valueText);
      if (Number.isFinite(time) && Number.isFinite(value)) {
        samples.push({ time, value });
      }
    }
  });
  return {
    samples,
    stop: () => {
      try {
        child.kill();
      } catch {
        // ignore
      }
    },
  };
}

function windowStats(samples, start, end) {
  const picked = samples.filter((item) => item.time >= start && item.time <= end);
  if (!picked.length) return { avg: 0, peak: 0, samples: 0 };
  const values = picked.map((item) => item.value);
  return {
    avg: Number((values.reduce((a, b) => a + b, 0) / values.length).toFixed(2)),
    peak: Number(Math.max(...values).toFixed(2)),
    samples: values.length,
  };
}

async function runOnce(mode, gpu) {
  const targetUrl =
    `http://127.0.0.1:${PORT}/bench/terminal-bench.html?renderer=${mode.renderer}` +
    (mode.batch ? `&batch=${mode.batch}` : "") +
    (mode.hidden ? "&hidden=1" : "");
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
    const startedAt = Date.now();
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
    const endedAt = Date.now();
    const metric = (snapshot, name) =>
      snapshot.metrics.find((item) => item.name === name)?.value ?? 0;
    const cpuSeconds = Number(
      (metric(final, "TaskDuration") - metric(baseline, "TaskDuration")).toFixed(3),
    );
    const gpuStats = gpu ? windowStats(gpu.samples, startedAt, endedAt) : null;
    return result
      ? { ...result, hidden: Boolean(mode.hidden), cpuSeconds, gpu: gpuStats }
      : { renderer: mode.renderer, error: "timeout" };
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
    const pids = await msedgePids(edge.pid);
    const gpu = startGpuSampler(pids);
    const results = [];
    for (const mode of [
      { renderer: "default" },
      { renderer: "webgl" },
      { renderer: "default", batch: 24 },
      { renderer: "webgl", batch: 24 },
      { renderer: "default", hidden: true },
      { renderer: "webgl", batch: 24, hidden: true },
    ]) {
      results.push(await runOnce(mode, gpu));
    }
    gpu.stop();
    console.log(JSON.stringify({ pids, cores: os.cpus().length, results }, null, 2));
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
