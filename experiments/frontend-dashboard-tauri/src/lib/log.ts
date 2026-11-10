export type LogLevel = "info" | "warn" | "error";

export interface LogEntry {
  id: number;
  time: string;
  level: LogLevel;
  text: string;
  /** 后端日志来源角色；客户端事件为空。 */
  role?: string;
}

/** 客户端事件（连接/SSE/停止）在过滤器中使用的伪角色。 */
export const CLIENT_ROLE = "client";

function pad(value: number): string {
  return String(value).padStart(2, "0");
}

export function makeLogEntry(
  id: number,
  level: LogLevel,
  text: string,
  at: Date = new Date(),
  role?: string,
): LogEntry {
  const entry: LogEntry = {
    id,
    time: `${pad(at.getHours())}:${pad(at.getMinutes())}:${pad(at.getSeconds())}`,
    level,
    text,
  };
  const label = role?.trim();
  if (label) entry.role = label;
  return entry;
}

export function entryRole(entry: LogEntry): string {
  return entry.role?.trim() || CLIENT_ROLE;
}

/** 当前日志中出现过的角色，去重排序；客户端事件固定为 CLIENT_ROLE。 */
export function availableRoles(logs: LogEntry[]): string[] {
  return [...new Set(logs.map(entryRole))].sort((a, b) => a.localeCompare(b));
}

/** 按角色与关键字过滤；query 不区分大小写，匹配正文或角色名。 */
export function filterLogs(logs: LogEntry[], role: string, query: string): LogEntry[] {
  const needle = query.trim().toLowerCase();
  return logs.filter((entry) => {
    if (role && entryRole(entry) !== role) return false;
    if (!needle) return true;
    return (
      entry.text.toLowerCase().includes(needle) || entryRole(entry).toLowerCase().includes(needle)
    );
  });
}

/** 事件日志最多保留最近 limit 条，避免长时间运行无限增长。 */
export function appendBounded(logs: LogEntry[], entry: LogEntry, limit = 500): LogEntry[] {
  const next = [...logs, entry];
  return next.length > limit ? next.slice(next.length - limit) : next;
}

/** 后端日志行 → Console 文本（带来源前缀）。 */
export function logEventText(role: string | undefined, line: string): string {
  const label = role?.trim() || "log";
  return `[${label}] ${line}`;
}
