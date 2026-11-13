import { invoke } from "@tauri-apps/api/core";
import { join } from "@tauri-apps/api/path";
import { readTextFile } from "@tauri-apps/plugin-fs";
import { fetch as tauriFetch } from "@tauri-apps/plugin-http";

import type {
  Agent,
  HandoffStatus,
  PreflightResult,
  StatusSnapshot,
  TaskList,
  TokenUsage,
  WildProcess,
  Workspace,
  WorkspacesResponse,
} from "./types";
import type { ChatBlock } from "./chat";
import { errorMessage } from "./format";

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

let daemonStart: Promise<void> | null = null;

export function projectRoot(): string {
  return import.meta.env.VITE_AGENT_SENTINEL_ROOT?.trim() ?? "";
}

/** 项目默认 orchestrator home：`.claude/orchestrator`（控制面 / 默认工作区 daemon）。 */
export async function orchestratorHome(): Promise<string> {
  const root = projectRoot();
  if (!root) {
    throw new Error("未配置 VITE_AGENT_SENTINEL_ROOT（AgentSentinel 项目根目录）");
  }
  return join(root, ".claude", "orchestrator");
}

let activeHome: string | null = null;

/** 切换当前工作区 daemon home；未设置时回落到控制面。 */
export function setDaemonHome(home: string | null): void {
  activeHome = home;
}

export async function currentDaemonHome(): Promise<string> {
  return activeHome ?? (await orchestratorHome());
}

export interface DaemonInfo {
  host: string;
  port: number;
  pid: number;
}

/** 读取指定 home 的 daemon.json；daemon 重启换端口后也能跟上。 */
export async function readDaemonInfoAt(home: string): Promise<DaemonInfo | null> {
  try {
    const raw = await readTextFile(await join(home, "daemon.json"));
    const info = JSON.parse(raw) as { host?: unknown; port?: unknown; pid?: unknown };
    if (typeof info.host === "string" && typeof info.port === "number") {
      return {
        host: info.host,
        port: info.port,
        pid: typeof info.pid === "number" ? info.pid : 0,
      };
    }
  } catch {
    // daemon.json 尚不存在或不可读
  }
  return null;
}

export async function readDaemonInfo(): Promise<DaemonInfo | null> {
  return readDaemonInfoAt(await currentDaemonHome());
}

export async function daemonBaseAt(home: string): Promise<string> {
  const info = await readDaemonInfoAt(home);
  return info ? `http://${info.host}:${info.port}` : FALLBACK_BASE;
}

/** 读取当前 daemon.json 得到实际 host:port。 */
export async function daemonBase(): Promise<string> {
  return daemonBaseAt(await currentDaemonHome());
}

async function healthy(base: string): Promise<boolean> {
  try {
    const response = await tauriFetch(`${base}/health`);
    return response.ok;
  } catch {
    return false;
  }
}

export async function healthyAt(home: string): Promise<boolean> {
  const info = await readDaemonInfoAt(home);
  if (!info) return false;
  return healthy(`http://${info.host}:${info.port}`);
}

/** 控制面 daemon（默认 home）必须在线，用于配置中心与工作区列表。 */
async function startDaemonOnce(): Promise<void> {
  const controlHome = await orchestratorHome();
  if (await healthyAt(controlHome)) return;
  const root = projectRoot();
  if (!root) throw new Error("未配置 VITE_AGENT_SENTINEL_ROOT（AgentSentinel 项目根目录）");
  try {
    await invoke("start_daemon", { root });
  } catch (error) {
    throw new Error(`daemon 启动失败：${errorMessage(error)}`);
  }
  const deadline = Date.now() + 20_000;
  while (Date.now() < deadline) {
    if (await healthyAt(controlHome)) return;
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  throw new Error(`daemon 启动超时（root=${root}）`);
}

/** 确保指定工作区的独立 daemon 在线；离线则静默拉起。 */
export async function ensureWorkspaceDaemon(workspace: Workspace): Promise<void> {
  const home = workspace.daemon_home;
  if (!home) return;
  if (await healthyAt(home)) return;
  const root = projectRoot();
  if (!root) throw new Error("未配置 VITE_AGENT_SENTINEL_ROOT（AgentSentinel 项目根目录）");
  try {
    await invoke("start_daemon", {
      root,
      home,
      project: workspace.project_path,
      workspaceId: workspace.id,
    });
  } catch (error) {
    throw new Error(`工作区 daemon 启动失败：${errorMessage(error)}`);
  }
  const deadline = Date.now() + 20_000;
  while (Date.now() < deadline) {
    if (await healthyAt(home)) return;
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  throw new Error(`工作区 daemon 启动超时（${workspace.name}）`);
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

export interface ShutdownResult {
  exited: boolean;
  pid: number;
}

/** 请求指定 home 的 daemon 优雅关闭并等待清理完成。 */
export async function shutdownHome(home: string, timeoutMs = 8_000): Promise<ShutdownResult> {
  const info = await readDaemonInfoAt(home);
  const pid = info?.pid ?? 0;
  if (info === null) return { exited: true, pid };
  const base = `http://${info.host}:${info.port}`;
  try {
    await tauriFetch(`${base}/shutdown`, {
      method: "POST",
      headers: { ...(await tokenHeaderAt(home)), "Content-Type": "application/json" },
      body: "{}",
    });
  } catch {
    // daemon 已离线时无需再发
  }
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (!(await healthy(base))) return { exited: true, pid };
    await new Promise((resolve) => setTimeout(resolve, 200));
  }
  return { exited: false, pid };
}

/** 当前 daemon；超时强杀兜底。 */
export async function shutdownDaemon(timeoutMs = 12_000): Promise<ShutdownResult> {
  const home = await currentDaemonHome();
  const result = await shutdownHome(home, timeoutMs);
  if (!result.exited && result.pid > 0) {
    try {
      await forceKillPid(result.pid);
    } catch {
      // taskkill 失败时交给 clean_all
    }
  }
  return result;
}

/** 关窗：关闭控制面 + 所有已配置工作区的 daemon，确保无残留。 */
export async function shutdownAllDaemons(): Promise<void> {
  const homes = new Set<string>();
  try {
    homes.add(await orchestratorHome());
  } catch {
    // 未配置 root
  }
  let workspaces: Workspace[] = [];
  try {
    const data = await getWorkspaces();
    workspaces = data.workspaces ?? [];
  } catch {
    // 控制面离线时仍尝试关闭当前 home
  }
  for (const workspace of workspaces) {
    if (workspace.daemon_home) homes.add(workspace.daemon_home);
  }
  try {
    homes.add(await currentDaemonHome());
  } catch {
    // ignore
  }
  for (const home of homes) {
    const result = await shutdownHome(home);
    if (!result.exited && result.pid > 0) {
      try {
        await forceKillPid(result.pid);
      } catch {
        // 交给 clean_all
      }
    }
  }
}

/** 兜底：daemon 超时未退出时强杀整棵进程树（含 PTY / claude）。 */
export async function forceKillPid(pid: number): Promise<void> {
  if (pid <= 0) return;
  await invoke("force_kill_pid", { pid });
}

async function tokenHeaderAt(home: string): Promise<Record<string, string>> {
  try {
    const token = (await readTextFile(await join(home, "token"))).trim();
    return token ? { Authorization: `Bearer ${token}` } : {};
  } catch {
    return {};
  }
}

async function tokenHeader(): Promise<Record<string, string>> {
  return tokenHeaderAt(await currentDaemonHome());
}

async function requestJSONAt<T>(
  home: string,
  path: string,
  init: RequestInit = {},
): Promise<T> {
  const headers: Record<string, string> = {
    Accept: "application/json",
    ...(await tokenHeaderAt(home)),
    ...(init.headers as Record<string, string> | undefined),
  };
  const response = await tauriFetch(`${await daemonBaseAt(home)}${path}`, { ...init, headers });
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

async function requestJSON<T>(path: string, init: RequestInit = {}): Promise<T> {
  return requestJSONAt(await currentDaemonHome(), path, init);
}

/** 配置中心/工作区列表固定走控制面 daemon。 */
async function requestControlJSON<T>(path: string, init: RequestInit = {}): Promise<T> {
  return requestJSONAt(await orchestratorHome(), path, init);
}

export function getStatus(): Promise<StatusSnapshot> {
  return requestJSON<StatusSnapshot>("/status");
}

export function getTasks(): Promise<TaskList> {
  return requestJSON<TaskList>("/tasks");
}

export interface DiscoverResult {
  discovered?: boolean;
  recovered?: number;
  pruned?: number;
  agents?: Agent[];
  wild?: WildProcess[];
}

/** 手动检测：让 daemon 重新探测已登记 agent 的进程存活。 */
export function discoverAgents(): Promise<DiscoverResult> {
  return requestJSON<DiscoverResult>("/discover", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: "{}",
  });
}

export async function stopRole(role: string, agentId?: string): Promise<void> {
  await requestJSON<unknown>("/stop", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ role, agent_id: agentId ?? "" }),
  });
}

export function getWorkspaces(): Promise<WorkspacesResponse> {
  return requestControlJSON<WorkspacesResponse>("/workspaces");
}

function workspaceAction(path: string, body: Record<string, unknown>): Promise<WorkspacesResponse> {
  return requestControlJSON<WorkspacesResponse>(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export function saveWorkspace(
  payload: Record<string, unknown>,
): Promise<WorkspacesResponse> {
  return workspaceAction("/workspaces/save", payload);
}

export function deleteWorkspace(id: number): Promise<WorkspacesResponse> {
  return workspaceAction("/workspaces/delete", { id });
}

export function activateWorkspace(id: number): Promise<WorkspacesResponse> {
  return workspaceAction("/workspaces/activate", { id });
}

export function preflightWorkspace(
  command: string,
  projectPath: string,
): Promise<PreflightResult> {
  return requestControlJSON<PreflightResult>("/workspaces/preflight", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ command, project_path: projectPath }),
  });
}

export function getHandoff(): Promise<HandoffStatus> {
  return requestJSON<HandoffStatus>("/handoff");
}

export function startHandoff(role: string, agentId: string): Promise<HandoffStatus> {
  return requestJSON<HandoffStatus>("/handoff", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ role, agent_id: agentId }),
  });
}

/** 强制清理未注册的 node/python 残留进程；create_time 用于防止 PID 复用误杀。 */
export function cleanupWildProcess(
  pid: number,
  createTime: number,
): Promise<{ cleaned?: boolean; pid?: number; killed?: number[] }> {
  return requestJSON("/wild/cleanup", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ pid, create_time: createTime }),
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

export interface SessionHistoryResponse {
  role: string;
  session_id: string;
  source: "terminal";
  messages: Array<{ role: string; blocks: ChatBlock[]; timestamp?: string | null }>;
  truncated: boolean;
  context_tokens: number;
  bytes: number;
}

/** 只读读取终端会话的本地 transcript（daemon 解析，零 token）。 */
export function getSessionHistory(role: string, limit = 200): Promise<SessionHistoryResponse> {
  return requestJSON<SessionHistoryResponse>(
    `/sessions/history?role=${encodeURIComponent(role)}&limit=${limit}`,
  );
}

export interface PtyStatus {
  running: boolean;
  pid: number;
  role?: string;
  cols: number;
  rows: number;
  exit_status: number | null;
  backlog_bytes: number;
  subscribers: number;
  command: string;
  session_id: string;
}

export function startPty(role?: string, agentId?: string): Promise<PtyStatus> {
  return requestJSON<PtyStatus>("/pty/start", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ role: role ?? "", agent_id: agentId ?? "" }),
  });
}

export function sendPtyInput(data: string, role?: string, agentId?: string): Promise<void> {
  return requestJSON<unknown>("/pty/input", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ data, role: role ?? "", agent_id: agentId ?? "" }),
  }).then(() => undefined);
}

export function resizePty(
  cols: number,
  rows: number,
  role?: string,
  agentId?: string,
): Promise<PtyStatus> {
  return requestJSON<PtyStatus>("/pty/resize", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ cols, rows, role: role ?? "", agent_id: agentId ?? "" }),
  });
}

export async function ptyStreamUrl(role?: string, agentId?: string): Promise<string> {
  return `${await daemonBase()}/pty/stream?role=${encodeURIComponent(role ?? "")}&agent_id=${encodeURIComponent(agentId ?? "")}`;
}

export function stopPty(role?: string, agentId?: string): Promise<void> {
  return requestJSON<unknown>("/pty/stop", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ role: role ?? "", agent_id: agentId ?? "" }),
  }).then(() => undefined);
}

export interface PtyHistoryResponse {
  session_id: string;
  source: "pty";
  messages: Array<{ role: string; blocks: ChatBlock[]; timestamp?: string | null }>;
  truncated: boolean;
  context_tokens: number;
  usage?: TokenUsage;
  budget_tokens?: number;
  bytes: number;
  offset: number;
}

export function getPtyHistory(role?: string, agentId?: string): Promise<PtyHistoryResponse> {
  return requestJSON<PtyHistoryResponse>(
    `/pty/history?role=${encodeURIComponent(role ?? "")}&agent_id=${encodeURIComponent(agentId ?? "")}`,
  );
}

export async function ptyChatStreamUrl(
  offset: number,
  role?: string,
  agentId?: string,
): Promise<string> {
  return `${await daemonBase()}/pty/chat/stream?offset=${offset}&role=${encodeURIComponent(role ?? "")}&agent_id=${encodeURIComponent(agentId ?? "")}`;
}

/** 发起某一角色的一轮聊天，返回可直接读取的 SSE 响应流。 */
export async function startChatStream(options: {
  role: string;
  prompt: string;
  signal: AbortSignal;
}): Promise<Response> {
  const response = await streamFetch(`${await daemonBase()}/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
    body: JSON.stringify({ role: options.role, prompt: options.prompt }),
    signal: options.signal,
  });
  if (!response.ok) throw new ApiError(response.status, `HTTP ${response.status}`);
  return response;
}
