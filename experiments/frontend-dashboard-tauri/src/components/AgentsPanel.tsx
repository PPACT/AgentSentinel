import { Plus, RefreshCw, TerminalSquare, UserPlus } from "lucide-react";
import { useState } from "react";

import { AgentTable } from "@/components/AgentTable";
import { WildTable } from "@/components/WildTable";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import type { Agent, WildProcess } from "@/lib/types";

interface AgentsPanelProps {
  agents: Agent[];
  pendingRole: string | null;
  onStop: (role: string) => void;
  selectedRole: string | null;
  onSelectRole: (role: string) => void;
  onNewAgent: () => void;
  onEditAgent: (role: string) => void;
  onDeleteAgent: (role: string) => void;
  wild: WildProcess[];
  wildOpen: boolean;
  onToggleWild: (open: boolean) => void;
  discovering: boolean;
  onDiscover: () => void;
  onCleanupWild: (process: WildProcess) => void;
  cleaningPid: number | null;
}

/** 左侧 Agent 侧栏：+ 菜单、角色列表与折叠的未注册进程。 */
export function AgentsPanel({
  agents,
  pendingRole,
  onStop,
  selectedRole,
  onSelectRole,
  onNewAgent,
  onEditAgent,
  onDeleteAgent,
  wild,
  wildOpen,
  onToggleWild,
  discovering,
  onDiscover,
  onCleanupWild,
  cleaningPid,
}: AgentsPanelProps) {
  const [menuOpen, setMenuOpen] = useState(false);

  return (
    <section className="flex h-full min-h-0 min-w-0 flex-col px-2.5 pb-2.5 pt-3">
      <div className="mb-2 flex items-center justify-between gap-1 px-0.5">
        <h2 className="text-[10px] font-medium uppercase tracking-[0.14em] text-zinc-500">
          Agents
        </h2>
        <div className="flex items-center gap-0.5">
          <button
            type="button"
            aria-label="检测 Agent"
            title="检测 Agent"
            disabled={discovering}
            onClick={onDiscover}
            className="rounded p-1 text-zinc-500 transition-colors hover:bg-zinc-900 hover:text-zinc-200 disabled:opacity-40"
          >
            <RefreshCw className={`size-3.5 ${discovering ? "animate-spin" : ""}`} />
          </button>
          <Popover open={menuOpen} onOpenChange={setMenuOpen}>
            <PopoverTrigger asChild>
              <button
                type="button"
                aria-label="新建"
                title="新建"
                className="rounded border border-zinc-800/80 p-1 text-zinc-400 transition-colors hover:border-zinc-700 hover:bg-zinc-900 hover:text-zinc-100 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-sky-500/60"
              >
                <Plus className="size-3.5" />
              </button>
            </PopoverTrigger>
            <PopoverContent
              align="end"
              sideOffset={6}
              className="w-56 border-zinc-800/80 bg-zinc-950 p-1 text-zinc-200 shadow-xl"
            >
              <button
                type="button"
                className="flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-[12px] transition-colors hover:bg-zinc-900"
                onClick={() => {
                  setMenuOpen(false);
                  onNewAgent();
                }}
              >
                <UserPlus className="size-3.5 text-sky-400/80" />
                新建 Agent 角色
              </button>
              <button
                type="button"
                disabled
                title="需要多终端（PTY 池），当前版本仅支持单终端切换"
                className="flex w-full cursor-not-allowed items-center gap-2 rounded px-2 py-1.5 text-left text-[12px] text-zinc-600"
              >
                <TerminalSquare className="size-3.5" />
                <span className="flex-1">新建本地终端</span>
                <span className="text-[10px]">后续</span>
              </button>
            </PopoverContent>
          </Popover>
        </div>
      </div>

      <div className="min-h-0 flex-1 overflow-auto">
        <AgentTable
          agents={agents}
          pendingRole={pendingRole}
          onStop={onStop}
          onSelect={onSelectRole}
          selectedRole={selectedRole}
          onEdit={onEditAgent}
          onDelete={onDeleteAgent}
        />
      </div>

      {wild.length > 0 ? (
        <details
          className="mt-2 rounded-md border border-zinc-900 bg-zinc-950/60"
          open={wildOpen}
          onToggle={(event) => onToggleWild(event.currentTarget.open)}
        >
          <summary className="cursor-pointer select-none px-2 py-1 text-[10px] font-medium text-amber-300/80">
            未注册进程（{wild.length}）
          </summary>
          <div className="max-h-36 overflow-auto border-t border-zinc-900">
            <WildTable processes={wild} onCleanup={onCleanupWild} busyPid={cleaningPid} />
          </div>
        </details>
      ) : null}
    </section>
  );
}
