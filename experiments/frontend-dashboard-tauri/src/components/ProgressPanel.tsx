import { FileText, Search, Terminal, Wrench } from "lucide-react";

import type { ChatEntry } from "@/lib/chat";

interface ProgressPanelProps {
  role: string | null;
  entries: ChatEntry[];
}

type ToolKind = "bash" | "edit" | "read" | "other";

interface ToolEvent {
  id: number;
  name: string;
  input: string;
  kind: ToolKind;
  time: string;
}

const EDIT_TOOLS = new Set(["edit", "write", "multiedit"]);
const READ_TOOLS = new Set(["read", "grep", "glob"]);

function classify(name: string): ToolKind {
  const tool = name.toLowerCase();
  if (tool === "bash") return "bash";
  if (EDIT_TOOLS.has(tool)) return "edit";
  if (READ_TOOLS.has(tool)) return "read";
  return "other";
}

function timeLabel(value?: string | null): string {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleTimeString("zh-CN", { hour12: false });
}

const KIND_META: Record<ToolKind, { label: string; icon: typeof Terminal; className: string }> = {
  bash: { label: "命令", icon: Terminal, className: "text-emerald-400/80 border-emerald-900/60" },
  edit: { label: "文件", icon: FileText, className: "text-sky-400/80 border-sky-900/60" },
  read: { label: "读取", icon: Search, className: "text-zinc-400 border-zinc-700/60" },
  other: { label: "工具", icon: Wrench, className: "text-amber-400/80 border-amber-900/60" },
};

/** 进度记录：把工具调用整理成时间轴，聚焦“改了哪些文件、跑了哪些命令”。 */
export function ProgressPanel({ role, entries }: ProgressPanelProps) {
  const events: ToolEvent[] = entries
    .filter((entry) => entry.block.kind === "tool")
    .map((entry) => {
      const block = entry.block;
      if (block.kind !== "tool") throw new Error("unreachable");
      return {
        id: entry.id,
        name: block.name,
        input: block.input,
        kind: classify(block.name),
        time: timeLabel(entry.timestamp),
      };
    });

  const commandCount = events.filter((event) => event.kind === "bash").length;
  const files = new Set(
    events
      .filter((event) => event.kind === "edit")
      .map((event) => event.input)
      .filter(Boolean),
  );
  const readCount = events.filter((event) => event.kind === "read").length;

  return (
    <section className="flex h-full min-h-0 min-w-0 flex-col border-x border-zinc-800 bg-zinc-950">
      <header className="shrink-0 border-b border-zinc-800 px-4 py-2">
        <div className="flex items-center justify-between gap-3">
          <div className="flex items-center gap-2">
            <h2 className="text-xs font-semibold uppercase tracking-wider text-zinc-300">
              进度记录
            </h2>
            {role ? (
              <span className="rounded border border-zinc-700/70 px-1.5 py-0.5 font-mono text-[10px] text-zinc-500">
                {role}
              </span>
            ) : null}
          </div>
          <div className="flex items-center gap-3 font-mono text-[10px] text-zinc-600">
            <span>工具 {events.length}</span>
            <span>命令 {commandCount}</span>
            <span>文件 {files.size}</span>
            <span>读取 {readCount}</span>
          </div>
        </div>
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">
        {!role ? (
          <div className="flex h-full flex-col items-center justify-center gap-1 text-center">
            <div className="text-sm text-zinc-400">请先在左侧选择角色</div>
            <div className="text-xs text-zinc-600">进度来自该角色的终端历史与本窗口对话</div>
          </div>
        ) : events.length === 0 ? (
          <div className="flex h-full flex-col items-center justify-center gap-1 text-center">
            <div className="text-sm text-zinc-400">暂无进度记录</div>
            <div className="text-xs text-zinc-600">工具调用、文件修改与命令会显示在这里</div>
          </div>
        ) : (
          <div className="relative ml-1 border-l border-zinc-800 pl-5">
            {events.map((event) => {
              const meta = KIND_META[event.kind];
              const Icon = meta.icon;
              return (
                <div key={event.id} className="relative pb-4">
                  <span className="absolute -left-[26px] top-1.5 size-2 rounded-full border border-zinc-700 bg-zinc-950" />
                  <div className="flex min-w-0 items-center gap-2">
                    {event.time ? (
                      <span className="font-mono text-[10px] text-zinc-700">{event.time}</span>
                    ) : null}
                    <span
                      className={`inline-flex items-center gap-1 rounded border px-1 py-0.5 text-[9px] ${meta.className}`}
                    >
                      <Icon className="size-2.5" />
                      {meta.label}
                    </span>
                    <span className="truncate font-mono text-[11px] text-zinc-300">{event.name}</span>
                  </div>
                  <div
                    className="mt-0.5 truncate font-mono text-[11px] text-zinc-500"
                    title={event.input}
                  >
                    {event.input || "—"}
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </section>
  );
}
