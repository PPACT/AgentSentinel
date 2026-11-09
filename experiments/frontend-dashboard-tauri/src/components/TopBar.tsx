import { Button } from "@/components/ui/button";
import { connectionLabel, daemonSummary } from "@/lib/format";
import type { ConnectionState, DaemonInfo } from "@/lib/types";

interface TopBarProps {
  daemon?: DaemonInfo;
  connection: ConnectionState;
  onOpenCommand: () => void;
}

export function TopBar({ daemon, connection, onOpenCommand }: TopBarProps) {
  const open = connection === "open";
  return (
    <header className="flex items-center justify-between gap-4 border-b border-zinc-800 bg-zinc-900/70 px-5 py-3">
      <div className="flex min-w-0 items-baseline gap-3">
        <h1 className="text-lg font-semibold tracking-wide text-zinc-100">AgentSentinel 中控</h1>
        <span className="truncate font-mono text-xs text-zinc-400">{daemonSummary(daemon)}</span>
      </div>
      <div className="flex shrink-0 items-center gap-3">
        <span
          className={`inline-flex items-center gap-2 rounded-full border px-3 py-1 text-xs ${
            open
              ? "border-emerald-500/40 text-emerald-400"
              : "border-amber-500/40 text-amber-400"
          }`}
        >
          <span className={`size-1.5 rounded-full ${open ? "bg-emerald-400" : "bg-amber-400"}`} />
          {connectionLabel(connection)}
        </span>
        <Button variant="outline" size="sm" className="font-mono text-xs" onClick={onOpenCommand}>
          Ctrl+K 命令
        </Button>
      </div>
    </header>
  );
}
