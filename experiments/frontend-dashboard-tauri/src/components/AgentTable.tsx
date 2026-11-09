import { Button } from "@/components/ui/button";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { formatAgent, localTimestamp } from "@/lib/format";
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
            return (
              <TableRow key={`${role}-${row.pid}`}>
                <TableCell className="font-medium text-zinc-100">{row.role}</TableCell>
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
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={!role || pending}
                    onClick={() => onStop(role)}
                  >
                    {pending ? "停止中…" : "停止"}
                  </Button>
                </TableCell>
              </TableRow>
            );
          })
        )}
      </TableBody>
    </Table>
  );
}
