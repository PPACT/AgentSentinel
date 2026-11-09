import { useEffect, useRef } from "react";
import { useVirtualizer } from "@tanstack/react-virtual";

import type { LogEntry, LogLevel } from "@/lib/log";

interface ConsolePanelProps {
  logs: LogEntry[];
}

const LEVEL_CLASS: Record<LogLevel, string> = {
  info: "text-zinc-300",
  warn: "text-amber-400",
  error: "text-red-400",
};

const LEVEL_LABEL: Record<LogLevel, string> = {
  info: "INFO",
  warn: "WARN",
  error: "ERR ",
};

/** 底部 Console：展示客户端事件（连接/SSE/停止操作）；后端日志流属 C2，待接口。 */
export function ConsolePanel({ logs }: ConsolePanelProps) {
  const parentRef = useRef<HTMLDivElement>(null);
  const virtualizer = useVirtualizer({
    count: logs.length,
    getScrollElement: () => parentRef.current,
    estimateSize: () => 20,
    overscan: 10,
  });

  useEffect(() => {
    const element = parentRef.current;
    if (element) element.scrollTop = element.scrollHeight;
  }, [logs.length]);

  return (
    <section className="flex h-full flex-col bg-zinc-900/60">
      <div className="flex items-center justify-between border-b border-zinc-800 px-4 py-2 text-xs uppercase tracking-wider text-zinc-500">
        <span>Console</span>
        <span>客户端事件 · 后端日志流 C2 待接</span>
      </div>
      <div
        ref={parentRef}
        className="min-h-0 flex-1 overflow-auto px-4 py-2 font-mono text-xs"
      >
        {logs.length === 0 ? (
          <div className="py-6 text-center text-zinc-500">暂无事件</div>
        ) : (
          <div style={{ height: `${virtualizer.getTotalSize()}px`, position: "relative" }}>
            {virtualizer.getVirtualItems().map((item) => {
              const entry = logs[item.index];
              if (!entry) return null;
              return (
                <div
                  key={entry.id}
                  style={{
                    position: "absolute",
                    top: 0,
                    left: 0,
                    width: "100%",
                    height: `${item.size}px`,
                    transform: `translateY(${item.start}px)`,
                  }}
                  className="whitespace-pre"
                >
                  <span className="text-zinc-500">{entry.time} </span>
                  <span className={LEVEL_CLASS[entry.level]}>{LEVEL_LABEL[entry.level]} </span>
                  <span className={LEVEL_CLASS[entry.level]}>{entry.text}</span>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </section>
  );
}
