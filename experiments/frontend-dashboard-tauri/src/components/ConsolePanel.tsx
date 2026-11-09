import { useMemo, useRef } from "react";
import { useVirtualizer } from "@tanstack/react-virtual";

const PLACEHOLDER_LINES = Array.from(
  { length: 200 },
  (_, index) => `[console] 日志流尚未接入（阶段二）· 占位行 ${String(index + 1).padStart(3, "0")}`,
);

/** 底部可折叠 Console：当前为日志流占位，用虚拟滚动验证大列表渲染。 */
export function ConsolePanel() {
  const parentRef = useRef<HTMLDivElement>(null);
  const lines = useMemo(() => PLACEHOLDER_LINES, []);
  const virtualizer = useVirtualizer({
    count: lines.length,
    getScrollElement: () => parentRef.current,
    estimateSize: () => 20,
    overscan: 8,
  });

  return (
    <section className="flex h-full flex-col bg-zinc-900/60">
      <div className="flex items-center justify-between border-b border-zinc-800 px-4 py-2 text-xs uppercase tracking-wider text-zinc-500">
        <span>Console</span>
        <span>日志流占位 · 虚拟滚动</span>
      </div>
      <div ref={parentRef} className="min-h-0 flex-1 overflow-auto px-4 py-2 font-mono text-xs text-zinc-400">
        <div style={{ height: `${virtualizer.getTotalSize()}px`, position: "relative" }}>
          {virtualizer.getVirtualItems().map((item) => (
            <div
              key={item.key}
              style={{
                position: "absolute",
                top: 0,
                left: 0,
                width: "100%",
                height: `${item.size}px`,
                transform: `translateY(${item.start}px)`,
              }}
            >
              {lines[item.index]}
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}
