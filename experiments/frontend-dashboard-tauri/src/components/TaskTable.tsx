import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { formatTask, localTimestamp, shortTimestamp } from "@/lib/format";
import type { Task } from "@/lib/types";

interface TaskTableProps {
  tasks: Task[];
  onSelect?: (task: Task) => void;
}

/** 侧栏宽度有限：只保留四个核心列，意图/完整 ID 放进 tooltip。 */
const COLUMNS = [
  { key: "task_id", label: "任务", width: "w-[38%]", align: "text-left" },
  { key: "flow", label: "流转", width: "w-[24%]", align: "text-left" },
  { key: "status", label: "状态", width: "w-[20%]", align: "text-left" },
  { key: "updated_at", label: "更新", width: "w-[18%]", align: "text-right" },
] as const;

function timeOnly(value?: string): string {
  const short = shortTimestamp(value);
  const index = short.lastIndexOf(" ");
  const time = index >= 0 ? short.slice(index + 1) : short;
  return time.slice(0, 5);
}

export function TaskTable({ tasks, onSelect }: TaskTableProps) {
  return (
    <Table className="w-full table-fixed">
      <TableHeader>
        <TableRow>
          {COLUMNS.map((column) => (
            <TableHead
              key={column.key}
              className={`${column.width} px-2 text-[10px] font-medium text-zinc-400 ${column.align}`}
            >
              {column.label}
            </TableHead>
          ))}
        </TableRow>
      </TableHeader>
      <TableBody>
        {tasks.length === 0 ? (
          <TableRow>
            <TableCell colSpan={COLUMNS.length} className="py-8 text-center text-zinc-500">
              暂无任务
            </TableCell>
          </TableRow>
        ) : (
          tasks.map((task) => {
            const row = formatTask(task);
            const details = [
              `task_id=${row.taskId}`,
              `intent=${row.intent}`,
              `acceptance=${(task.acceptance ?? []).join(" | ") || "-"}`,
              `updated_at=${localTimestamp(task.updated_at)}`,
            ].join("\n");
            return (
              <TableRow
                key={`${row.taskId}-${row.updatedAt}`}
                className={onSelect ? "cursor-pointer hover:bg-zinc-800/40" : undefined}
                tabIndex={onSelect ? 0 : undefined}
                onClick={onSelect ? () => onSelect(task) : undefined}
                onKeyDown={
                  onSelect
                    ? (event) => {
                        if (event.key === "Enter" || event.key === " ") {
                          event.preventDefault();
                          onSelect(task);
                        }
                      }
                    : undefined
                }
                title={details}
              >
                <TableCell className="truncate px-2 py-1.5 font-mono text-[11px] text-zinc-200">
                  {row.taskId}
                </TableCell>
                <TableCell className="truncate px-2 py-1.5 font-mono text-[11px] text-zinc-300">
                  {row.fromRole} → {row.toRole}
                </TableCell>
                <TableCell className="truncate px-2 py-1.5 font-mono text-[11px] text-zinc-300">
                  {row.status}
                </TableCell>
                <TableCell className="truncate px-2 py-1.5 text-right font-mono text-[10px] text-zinc-500">
                  {timeOnly(task.updated_at)}
                </TableCell>
              </TableRow>
            );
          })
        )}
      </TableBody>
    </Table>
  );
}
