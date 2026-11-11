import { ListTodo, PanelLeft } from "lucide-react";

import { Button } from "@/components/ui/button";
import { connectionLabel, daemonSummary } from "@/lib/format";
import type { ConnectionState, DaemonInfo } from "@/lib/types";

interface TopBarProps {
  daemon?: DaemonInfo;
  connection: ConnectionState;
  onOpenCommand: () => void;
  showAgentsToggle?: boolean;
  onToggleAgents?: () => void;
  showTasksToggle?: boolean;
  onToggleTasks?: () => void;
}

export function TopBar({
  daemon,
  connection,
  onOpenCommand,
  showAgentsToggle = false,
  onToggleAgents,
  showTasksToggle = false,
  onToggleTasks,
}: TopBarProps) {
  const open = connection === "open";
  return (
    <header className="flex items-center justify-between gap-3 border-b border-zinc-800 bg-zinc-900/70 px-4 py-2.5">
      <div className="flex min-w-0 items-baseline gap-3">
        <h1 className="shrink-0 text-base font-semibold tracking-wide text-zinc-100">
          AgentSentinel 中控
        </h1>
        <span className="hidden truncate font-mono text-[11px] text-zinc-500 sm:inline">
          {daemonSummary(daemon)}
        </span>
      </div>
      <div className="flex shrink-0 items-center gap-2">
        {showAgentsToggle ? (
          <Button
            aria-label="Agents"
            variant="outline"
            size="sm"
            className="h-7 px-2"
            title="Agents"
            onClick={onToggleAgents}
          >
            <PanelLeft className="size-3.5" />
          </Button>
        ) : null}
        {showTasksToggle ? (
          <Button
            aria-label="Tasks"
            variant="outline"
            size="sm"
            className="h-7 px-2"
            title="Tasks"
            onClick={onToggleTasks}
          >
            <ListTodo className="size-3.5" />
          </Button>
        ) : null}
        <span
          className={`inline-flex items-center gap-2 rounded-full border px-2.5 py-0.5 text-[11px] ${
            open
              ? "border-emerald-500/40 text-emerald-400"
              : "border-amber-500/40 text-amber-400"
          }`}
        >
          <span className={`size-1.5 rounded-full ${open ? "bg-emerald-400" : "bg-amber-400"}`} />
          {connectionLabel(connection)}
        </span>
        <Button
          variant="outline"
          size="sm"
          className="h-7 font-mono text-[11px]"
          onClick={onOpenCommand}
        >
          Ctrl+K
        </Button>
      </div>
    </header>
  );
}
