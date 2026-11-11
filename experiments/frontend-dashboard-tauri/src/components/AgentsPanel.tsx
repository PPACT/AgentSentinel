import { Button } from "@/components/ui/button";
import { AgentTable } from "@/components/AgentTable";
import { WildTable } from "@/components/WildTable";
import type { Agent, WildProcess } from "@/lib/types";

interface AgentsPanelProps {
  agents: Agent[];
  pendingRole: string | null;
  onStop: (role: string) => void;
  selectedRole: string | null;
  onSelectRole: (role: string) => void;
  wild: WildProcess[];
  wildOpen: boolean;
  onToggleWild: (open: boolean) => void;
  discovering: boolean;
  onDiscover: () => void;
  onCleanupWild: (process: WildProcess) => void;
  cleaningPid: number | null;
}

/** 左侧窄栏：只保留 Agent 核心信息，野生进程作为折叠区。 */
export function AgentsPanel({
  agents,
  pendingRole,
  onStop,
  selectedRole,
  onSelectRole,
  wild,
  wildOpen,
  onToggleWild,
  discovering,
  onDiscover,
  onCleanupWild,
  cleaningPid,
}: AgentsPanelProps) {
  return (
    <section className="flex h-full min-h-0 min-w-0 flex-col p-2.5">
      <div className="mb-2 flex items-center justify-between gap-1">
        <h2 className="text-[11px] font-semibold uppercase tracking-wider text-sky-400">Agents</h2>
        <Button
          variant="outline"
          size="sm"
          className="h-6 px-1.5 text-[10px]"
          disabled={discovering}
          onClick={onDiscover}
        >
          {discovering ? "检测中" : "检测"}
        </Button>
      </div>
      <div className="min-h-0 flex-1 overflow-auto rounded-md border border-zinc-800">
        <AgentTable
          agents={agents}
          pendingRole={pendingRole}
          onStop={onStop}
          onSelect={onSelectRole}
          selectedRole={selectedRole}
        />
      </div>
      {wild.length > 0 ? (
        <details
          className="mt-2 rounded-md border border-zinc-800 bg-zinc-900/40"
          open={wildOpen}
          onToggle={(event) => onToggleWild(event.currentTarget.open)}
        >
          <summary className="cursor-pointer select-none px-2 py-1 text-[10px] font-medium text-amber-300/90">
            未注册进程（{wild.length}）
          </summary>
          <div className="max-h-36 overflow-auto border-t border-zinc-800">
            <WildTable processes={wild} onCleanup={onCleanupWild} busyPid={cleaningPid} />
          </div>
        </details>
      ) : null}
    </section>
  );
}
