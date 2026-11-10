import { join } from "@tauri-apps/api/path";
import { readTextFile } from "@tauri-apps/plugin-fs";
import { fetch as tauriFetch } from "@tauri-apps/plugin-http";
import { Command, type Child } from "@tauri-apps/plugin-shell";

import type { Agent, StatusSnapshot, TaskList } from "./types";

/** 默认 daemon 未写 daemon.json 时的兜底地址（与 shell scope 一致）。 */
const FALLBACK_BASE = "http://127.0.0.1:8765";

export class ApiError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

let daemonChild: Child | null = null;
let daemonStart: Promise<void> | null = null;

export function projectRoot(): string {
  return import.meta.env.VITE_AGENT_SENTINEL_ROOT?.trim() ?? "";
}

/** 项目默认 orchestrator home：`.claude/orchestrator`（dev/doc/MCP 注册的同一个 daemon）。 */
export async function orchestratorHome(): Promise<string> {
  const root = projectRoot();
  if (!root) {
    throw new Error("未配置 VITE_AGENT_SENTINEL_ROOT（AgentSentinel 项目根目录）");
  }
  return join(root, ".claude", "orchestrator");
}

/** 读取项目 daemon.json 得到实际 host:port；daemon 重启换端口后也能跟上。 */
export async function daemonBase(): Promise<string> {
  try {
    const raw = await readTextFile(await join(await orchestratorHome(), "daemon.json"));
    const info = JSON.parse(raw) as { host?: unknown; port?: unknown };
    if (typeof info.host === "string" && typeof info.port === "number") {
      return `http://${info.host}:${info.port}`;
    }
  } catch {
    // daemon.json 尚不存在或不可读
  }
  return FALLBACK_BASE;
}

async function healthy(base: string): Promise<boolean> {
  try {
    const response = await tauriFetch(`${base}/health`);
    return response.ok;
  } catch {
    return false;
  }
}

/** 已在线则复用；否则用项目默认 home 在 8765 拉起 daemon。 */
async function startDaemonOnce(): Promise<void> {
  if (await healthy(await daemonBase())) return;
  const root = projectRoot();
  const home = await orchestratorHome();
  const command = Command.create(
    "start-daemon",
    ["run", "python", "-m", "tools.orchestrator.service", "--serve", "--port", "8765"],
    { cwd: root, env: { SENTINEL_ORCH_HOME: home, PYTHONUTF8: "1", PYTHONIOENCODING: "utf-8" } },
  );
  daemonChild = await command.spawn();
  const deadline = Date.now() + 20_000;
  while (Date.now() < deadline) {
    if (await healthy(await daemonBase())) return;
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  throw new Error(`daemon 启动超时（home=${home}）`);
}

/** StrictMode 双挂载/并发调用共享同一次启动，避免双拉 daemon 与端口竞争。 */
export function ensureDaemon(): Promise<void> {
  if (daemonStart === null) {
    daemonStart = startDaemonOnce().finally(() => {
      daemonStart = null;
    });
  }
  return daemonStart;
}

export async function stopDaemon(): Promise<void> {
  const child = daemonChild;
  daemonChild = null;
  if (child) {
    try {
      await child.kill();
    } catch {
      // 已退出，忽略
    }
  }
}

async function tokenHeader(): Promise<Record<string, string>> {
  try {
    const token = (await readTextFile(await join(await orchestratorHome(), "token"))).trim();
    return token ? { Authorization: `Bearer ${token}` } : {};
  } catch {
    return {};
  }
}

async function requestJSON<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = {
    Accept: "application/json",
    ...(await tokenHeader()),
    ...(init.headers as Record<string, string> | undefined),
  };
  const response = await tauriFetch(`${await daemonBase()}${path}`, { ...init, headers });
  const raw = await response.text();
  let payload: unknown = null;
  try {
    payload = raw ? JSON.parse(raw) : null;
  } catch {
    payload = null;
  }
  if (!response.ok) {
    const detail =
      payload && typeof payload === "object" && "error" in payload
        ? String((payload as { error: unknown }).error)
        : `HTTP ${response.status}`;
    throw new ApiError(response.status, detail);
  }
  return (payload ?? {}) as T;
}

export function getStatus(): Promise<StatusSnapshot> {
  return requestJSON<StatusSnapshot>("/status");
}

export function getTasks(): Promise<TaskList> {
  return requestJSON<TaskList>("/tasks");
}

export interface DiscoverResult {
  discovered?: boolean;
  agents?: Agent[];
}

/** 手动检测：让 daemon 重新探测已登记 agent 的进程存活。 */
export function discoverAgents(): Promise<DiscoverResult> {
  return requestJSON<DiscoverResult>("/discover", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: "{}",
  });
}

export async function stopRole(role: string): Promise<void> {
  await requestJSON<unknown>("/stop", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ role }),
  });
}

export async function eventStreamUrl(): Promise<string> {
  return `${await daemonBase()}/events`;
}

export async function logStreamUrl(): Promise<string> {
  return `${await daemonBase()}/logs/stream`;
}

/** plugin-http fetch（带 Bearer），供 SSE 读取循环使用。 */
export async function streamFetch(url: string, init: RequestInit): Promise<Response> {
  const headers = { ...(await tokenHeader()), ...(init.headers as Record<string, string> | undefined) };
  return tauriFetch(url, { ...init, headers });
}
