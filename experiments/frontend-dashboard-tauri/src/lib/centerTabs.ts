import { ListChecks, MessageSquare, Terminal } from "lucide-react";
import type { ComponentType } from "react";

export type CenterTabKey = "chat" | "progress" | "terminal";

export interface CenterTabDef {
  key: CenterTabKey;
  label: string;
  icon: ComponentType<{ className?: string }>;
  hint: string;
}

export const CENTER_TABS: readonly CenterTabDef[] = [
  { key: "chat", label: "对话", icon: MessageSquare, hint: "JSONL 旁路 · 零 token" },
  { key: "progress", label: "进度记录", icon: ListChecks, hint: "工具时间轴 · 零 token" },
  { key: "terminal", label: "原生终端", icon: Terminal, hint: "PTY 常驻 · 原始 ANSI" },
];
