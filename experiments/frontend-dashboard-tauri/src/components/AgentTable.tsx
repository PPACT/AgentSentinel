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
  onSelect?: (role: string) => void;
  selectedRole?: string | null;
}

const COLUMNS = ["Agent", "状态", "操作"];

const STATUS_LABEL: Record<string, string> = {
  registered: "在线",
  running: "运行",
  stopped: "已停止",
  lost: "失联",
  pending: "待注册",
};

export function AgentTable({
  agents,
  pendingRole,
  onStop,
  onSelect,
  selectedRole,
}: AgentTableProps) {
  return (
    <Table className="w-full table-fixed">
      <TableHeader>
        <TableRow>
          <TableHead className="w-[46%] px-2 text-[10px]">Agent</TableHead>
          <TableHead className="w-[32%] px-2 text-[10px]">状态</TableHead>
          <TableHead className="w-[22%] px-2 text-right text-[10px]">操作</TableHead>
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
            const local = agent.command === "workspace";
            const details = [
              `role=${row.role}`,
              `status=${row.status}`,
              `pid=${row.pid}`,
              `pgid=${row.pgid}`,
              `alive=${row.alive}`,
              `heartbeat=${localTimestamp(agent.last_heartbeat)}`,
              `task=${row.currentTask}`,
              `project=${row.project}`,
            ].join("\n");
            const statusLabel =
              agent.alive === false && row.status !== "stopped"
                ? "已停止"
                : (STATUS_LABEL[row.status] ?? row.status);
            const selected = selectedRole === role && Boolean(role);
            return (
              <TableRow
                key={`${role}-${row.pid}`}
                className={
                  onSelect
                    ? `cursor-pointer ${
                        selected ? "bg-zinc-800/50" : "hover:bg-zinc-800/30"
                      }`
                    : undefined
                }
                tabIndex={onSelect && role ? 0 : undefined}
                onClick={onSelect && role ? () => onSelect(role) : undefined}
                onKeyDown={
                  onSelect && role
                    ? (event) => {
                        if (event.key === "Enter" || event.key === " ") {
                          event.preventDefault();
                          onSelect(role);
                        }
                      }
                    : undefined
                }
              >
                <TableCell
                  className={`min-w-0 border-l-2 px-2 py-1.5 ${
                    selected ? "border-sky-500" : "border-transparent"
                  }`}
                  title={details}
                >
                  <div className="flex min-w-0 items-center gap-1">
                    <span className="truncate font-medium text-zinc-100">{row.role}</span>
                    <span
                      className={`shrink-0 rounded border px-1 text-[9px] ${
                        local
                          ? "border-sky-800/60 bg-sky-950/40 text-sky-300"
                          : "border-zinc-700 bg-zinc-900 text-zinc-500"
                      }`}
                    >
                      {local ? "本地" : "外部"}
                    </span>
                  </div>
                  {row.project !== "—" ? (
                    <div className="truncate font-mono text-[10px] text-zinc-600">
                      {shortProject(row.project)}
                    </div>
                  ) : null}
                </TableCell>
                <TableCell className="px-2 py-1.5" title={details}>
                  <div className="flex min-w-0 items-center gap-1.5">
                    <span
                      className={`size-1.5 shrink-0 rounded-full ${
                        agent.alive ? "bg-emerald-400" : "bg-zinc-600"
                      }`}
                    />
                    <span className="truncate font-mono text-[11px] text-zinc-300">
                      {statusLabel}
                    </span>
                  </div>
                </TableCell>
                <TableCell className="px-2 py-1.5 text-right">
                  {managed ? (
                    <Button
                      variant="outline"
                      size="sm"
                      className="h-6 px-1.5 text-[10px]"
                      disabled={!role || pending || agent.alive === false}
                      onClick={(event) => {
                        event.stopPropagation();
                        onStop(role);
                      }}
                    >
                      {pending ? "停止中…" : "停止"}
                    </Button>
                  ) : local ? (
                    <span
                      className="whitespace-nowrap text-[10px] text-sky-400/80"
                      title="配置角色（本地）：由中控按角色启动命令拉起，切换角色时自动回收"
                    >
                      本地
                    </span>
                  ) : (
                    <span
                      className="whitespace-nowrap text-[10px] text-zinc-600"
                      title="外部 Agent，由各自窗口管理；中控无法终止"
                    >
                      外部
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
