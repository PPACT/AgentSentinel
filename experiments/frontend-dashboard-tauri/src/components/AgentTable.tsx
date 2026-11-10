import { Button } from "@/components/ui/button";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { formatAgent, localTimestamp, shortProject } from "@/lib/format";
import type { Agent } from "@/lib/types";

interface AgentTableProps {
  agents: Agent[];
  pendingRole: string | null;
  onStop: (role: string) => void;
}

const COLUMNS = ["role", "pid", "pgid", "status", "alive", "last_heartbeat", "current_task_id", "操作"];

export function AgentTable({ agents, pendingRole, onStop }: AgentTableProps) {
  return (
    <Table>
      <TableHeader>
        <TableRow>
          {COLUMNS.map((column) => (
            <TableHead key={column}>{column}</TableHead>
          ))}
        </TableRow>
      </TableHeader>
      <TableBody>
        {agents.length === 0 ? (
          <TableRow>
            <TableCell colSpan={COLUMNS.length} className="py-8 text-center text-zinc-500">
              暂无 agent
            </TableCell>
          </TableRow>
        ) : (
          agents.map((agent) => {
            const row = formatAgent(agent);
            const role = agent.role ?? "";
            const pending = pendingRole === role;
            const managed = Boolean(agent.job_id);
            return (
              <TableRow key={`${role}-${row.pid}`}>
                <TableCell className="min-w-0 py-1 font-medium text-zinc-100">
                  <div className="truncate">{row.role}</div>
                  {row.project !== "—" ? (
                    <div
                      className="max-w-[9rem] truncate font-mono text-[10px] text-zinc-500"
                      title={row.project}
                    >
                      {shortProject(row.project)}
                    </div>
                  ) : null}
                </TableCell>
                <TableCell className="font-mono text-zinc-300">{row.pid}</TableCell>
                <TableCell className="font-mono text-zinc-300">{row.pgid}</TableCell>
                <TableCell className="font-mono text-zinc-300">{row.status}</TableCell>
                <TableCell className={agent.alive ? "text-emerald-400" : "text-zinc-500"}>
                  {row.alive}
                </TableCell>
                <TableCell
                  className="truncate font-mono text-xs text-zinc-400"
                  title={localTimestamp(agent.last_heartbeat)}
                >
                  {localTimestamp(agent.last_heartbeat)}
                </TableCell>
                <TableCell className="font-mono text-xs text-zinc-400">{row.currentTask}</TableCell>
                <TableCell>
                  {managed ? (
                    <Button
                      variant="outline"
                      size="sm"
                      disabled={!role || pending || agent.alive === false}
                      onClick={() => onStop(role)}
                    >
                      {pending ? "停止中…" : "停止"}
                    </Button>
                  ) : (
                    <span
                      className="whitespace-nowrap text-[10px] text-zinc-500"
                      title="外部 Agent，由各自窗口管理；中控无法终止"
                    >
                      外部进程
                    </span>
                  )}
                </TableCell>
              </TableRow>
            );
          })
        )}
      </TableBody>
    </Table>
  );
}
