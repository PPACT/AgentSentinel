import type { Agent, ConnectionState, DaemonInfo, Task } from "./types";

function text(value: unknown): string {
  if (value === null || value === undefined || value === "") return "—";
  return String(value);
}

export function formatAgent(agent: Agent | null | undefined) {
  return {
    role: text(agent?.role),
    pid: text(agent?.pid),
    pgid: text(agent?.pgid),
    status: text(agent?.status),
    alive: agent?.alive ? "在线" : "已停止",
    lastHeartbeat: text(agent?.last_heartbeat),
    currentTask: text(agent?.current_task_id),
  };
}

export function formatTask(task: Task | null | undefined) {
  return {
    taskId: text(task?.task_id),
    fromRole: text(task?.from_role),
    toRole: text(task?.to_role),
    intent: text(task?.intent),
    status: text(task?.status),
    updatedAt: text(task?.updated_at),
  };
}

function parseTimestamp(value: unknown): Date | null {
  const raw = text(value);
  if (raw === "—") return null;
  const date = new Date(raw);
  return Number.isNaN(date.getTime()) ? null : date;
}

function pad(part: number): string {
  return String(part).padStart(2, "0");
}

/** 完整本地时间：`YYYY-MM-DD HH:mm:ss`（跟随系统时区，如北京时间）；无法解析时原样返回。 */
export function localTimestamp(value: unknown): string {
  const date = parseTimestamp(value);
  if (!date) return text(value);
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(
    date.getHours(),
  )}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
}

/** 窄栏用短时间：`MM-DD HH:mm:ss`（本地时区）；无法解析时原样返回。 */
export function shortTimestamp(value: unknown): string {
  const date = parseTimestamp(value);
  if (!date) return text(value);
  return `${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(
    date.getMinutes(),
  )}:${pad(date.getSeconds())}`;
}

export function daemonSummary(daemon: DaemonInfo | null | undefined): string {
  if (!daemon) return "中控未启动";
  return `pid ${text(daemon.pid)} · ${text(daemon.host)}:${text(daemon.port)} · 启动于 ${localTimestamp(
    daemon.started_at,
  )}`;
}

const CONNECTION_LABELS: Record<ConnectionState, string> = {
  connecting: "连接中…",
  open: "已连接（SSE）",
  closed: "连接已关闭，正在重连…",
  error: "连接中断，正在重连…",
  stopped: "已停止",
};

export function connectionLabel(state: ConnectionState): string {
  return CONNECTION_LABELS[state] ?? "未知状态";
}

export function errorMessage(error: unknown): string {
  const status = typeof error === "object" && error !== null && "status" in error
    ? (error as { status?: unknown }).status
    : undefined;
  const message = error instanceof Error ? error.message : String(error ?? "未知错误");
  if (status === 401) return "未授权（401）：token 缺失或不匹配，请检查 orchestrator home 下的 token";
  if (status === 503) return "中控未启动：请检查 AGENT_SENTINEL_ROOT 与 daemon 启动日志";
  return `请求失败：${message}`;
}

export function isDaemonDown(error: unknown): boolean {
  return typeof error === "object" && error !== null && "status" in error
    ? (error as { status?: unknown }).status === 503
    : false;
}
