export type LogLevel = "info" | "warn" | "error";

export interface LogEntry {
  id: number;
  time: string;
  level: LogLevel;
  text: string;
}

function pad(value: number): string {
  return String(value).padStart(2, "0");
}

export function makeLogEntry(
  id: number,
  level: LogLevel,
  text: string,
  at: Date = new Date(),
): LogEntry {
  return {
    id,
    time: `${pad(at.getHours())}:${pad(at.getMinutes())}:${pad(at.getSeconds())}`,
    level,
    text,
  };
}

/** 事件日志最多保留最近 limit 条，避免长时间运行无限增长。 */
export function appendBounded(logs: LogEntry[], entry: LogEntry, limit = 500): LogEntry[] {
  const next = [...logs, entry];
  return next.length > limit ? next.slice(next.length - limit) : next;
}
