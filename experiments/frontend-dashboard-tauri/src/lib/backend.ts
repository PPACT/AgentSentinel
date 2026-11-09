import { appDataDir, join } from "@tauri-apps/api/path";
import { readTextFile } from "@tauri-apps/plugin-fs";
import { fetch as tauriFetch } from "@tauri-apps/plugin-http";
import { Command, type Child } from "@tauri-apps/plugin-shell";

import type { StatusSnapshot, TaskList } from "./types";

export const DAEMON_BASE = "http://127.0.0.1:8765";

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

export async function orchestratorHome(): Promise<string> {
  return join(await appDataDir(), "orchestrator");
}

async function healthy(): Promise<boolean> {
  try {
    const response = await tauriFetch(`${DAEMON_BASE}/health`);
    return response.ok;
  } catch {
    return false;
  }
}

/** 拉起隔离 daemon（cwd=项目根，home=appData/orchestrator）；已在线则跳过。 */
async function startDaemonOnce(): Promise<void> {
  if (await healthy()) return;
  const root = projectRoot();
  if (!root) {
    throw new Error("未配置 VITE_AGENT_SENTINEL_ROOT（AgentSentinel 项目根目录）");
  }
  const home = await orchestratorHome();
  const command = Command.create(
    "start-daemon",
    ["run", "python", "-m", "tools.orchestrator.service", "--serve", "--port", "8765"],
    { cwd: root, env: { SENTINEL_ORCH_HOME: home } },
  );
  daemonChild = await command.spawn();
  const deadline = Date.now() + 20_000;
  while (Date.now() < deadline) {
    if (await healthy()) return;
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  throw new Error(`daemon 启动超时（cwd=${root}）`);
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
  const response = await tauriFetch(`${DAEMON_BASE}${path}`, { ...init, headers });
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

export async function stopRole(role: string): Promise<void> {
  await requestJSON<unknown>("/stop", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ role }),
  });
}

/** plugin-http fetch（带 Bearer），供 SSE 读取循环使用。 */
export async function streamFetch(url: string, init: RequestInit): Promise<Response> {
  const headers = { ...(await tokenHeader()), ...(init.headers as Record<string, string> | undefined) };
  return tauriFetch(url, { ...init, headers });
}
