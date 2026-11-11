import { ChevronDown } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { useVirtualizer } from "@tanstack/react-virtual";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  availableRoles,
  CLIENT_ROLE,
  filterLogs,
  type LogEntry,
  type LogLevel,
} from "@/lib/log";

interface ConsolePanelProps {
  logs: LogEntry[];
  onClear?: () => void;
  onCollapse?: () => void;
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

/** 底部 Console：后端日志流 + 客户端事件；支持角色过滤、搜索与暂停自动滚动。 */
export function ConsolePanel({ logs, onClear, onCollapse }: ConsolePanelProps) {
  const parentRef = useRef<HTMLDivElement>(null);
  const [role, setRole] = useState("");
  const [query, setQuery] = useState("");
  const [autoScroll, setAutoScroll] = useState(true);

  const roles = useMemo(() => availableRoles(logs), [logs]);
  const filtered = useMemo(() => filterLogs(logs, role, query), [logs, role, query]);

  const virtualizer = useVirtualizer({
    count: filtered.length,
    getScrollElement: () => parentRef.current,
    estimateSize: () => 20,
    overscan: 10,
  });

  useEffect(() => {
    if (!autoScroll) return;
    const element = parentRef.current;
    if (element) element.scrollTop = element.scrollHeight;
  }, [filtered.length, autoScroll]);

  const toggleAutoScroll = () => {
    if (autoScroll) {
      setAutoScroll(false);
      return;
    }
    setAutoScroll(true);
    const element = parentRef.current;
    if (element) element.scrollTop = element.scrollHeight;
  };

  const handleScroll = () => {
    const element = parentRef.current;
    if (!element) return;
    const atBottom = element.scrollHeight - element.scrollTop - element.clientHeight < 8;
    if (!atBottom && autoScroll) setAutoScroll(false);
  };

  return (
    <section className="flex h-full flex-col bg-zinc-900/60">
      <div className="flex flex-wrap items-center gap-2 border-b border-zinc-800 px-3 py-2 text-xs">
        <span className="uppercase tracking-wider text-zinc-500">Console</span>
        <span className="font-mono text-zinc-600">
          {filtered.length}/{logs.length}
        </span>
        <div className="ml-auto flex items-center gap-1.5">
          <select
            aria-label="按角色过滤"
            value={role}
            onChange={(event) => setRole(event.target.value)}
            className="h-7 rounded-md border border-zinc-700 bg-zinc-900 px-1.5 font-mono text-xs text-zinc-300 outline-none focus-visible:border-zinc-500"
          >
            <option value="">全部角色</option>
            {roles.map((item) => (
              <option key={item} value={item}>
                {item === CLIENT_ROLE ? "客户端" : item}
              </option>
            ))}
          </select>
          <Input
            aria-label="搜索日志"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="搜索…"
            className="h-7 w-32 border-zinc-700 bg-zinc-900 font-mono text-xs md:text-xs"
          />
          <Button
            aria-label="切换自动滚动"
            variant="outline"
            size="sm"
            className="h-7 px-2 text-xs"
            onClick={toggleAutoScroll}
          >
            {autoScroll ? "自动滚动" : "已暂停"}
          </Button>
          {onClear ? (
            <Button variant="outline" size="sm" className="h-7 px-2 text-xs" onClick={onClear}>
              清空
            </Button>
          ) : null}
          {onCollapse ? (
            <Button
              aria-label="收起 Console"
              variant="ghost"
              size="sm"
              className="h-7 px-1.5 text-zinc-400"
              title="收起 Console"
              onClick={onCollapse}
            >
              <ChevronDown className="size-3.5" />
            </Button>
          ) : null}
        </div>
      </div>
      <div
        ref={parentRef}
        onScroll={handleScroll}
        className="min-h-0 flex-1 overflow-auto px-4 py-2 font-mono text-xs"
      >
        {filtered.length === 0 ? (
          <div className="py-6 text-center text-zinc-500">
            {logs.length === 0 ? "暂无事件" : "无匹配日志"}
          </div>
        ) : (
          <div style={{ height: `${virtualizer.getTotalSize()}px`, position: "relative" }}>
            {virtualizer.getVirtualItems().map((item) => {
              const entry = filtered[item.index];
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
