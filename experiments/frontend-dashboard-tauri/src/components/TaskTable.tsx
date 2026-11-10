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

/** 右侧 Tasks 栏较窄：合并收发角色、压缩列宽，窄于此宽度改为横向滚动。 */
const COLUMNS = [
  { key: "task_id", label: "任务", width: "w-[30%]" },
  { key: "flow", label: "流转", width: "w-[17%]" },
  { key: "intent", label: "意图", width: "w-[23%]" },
  { key: "status", label: "状态", width: "w-[12%]" },
  { key: "updated_at", label: "更新时间", width: "w-[18%]" },
] as const;

export function TaskTable({ tasks, onSelect }: TaskTableProps) {
  return (
    <Table className="min-w-[520px] table-fixed">
      <TableHeader>
        <TableRow>
          {COLUMNS.map((column) => (
            <TableHead
              key={column.key}
              className={`${column.width} text-[11px] font-medium text-zinc-400`}
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
              >
                <TableCell className="truncate font-mono text-xs text-zinc-200" title={row.taskId}>
                  {row.taskId}
                </TableCell>
                <TableCell
                  className="truncate font-mono text-xs text-zinc-300"
                  title={`${row.fromRole} → ${row.toRole}`}
                >
                  {row.fromRole} → {row.toRole}
                </TableCell>
                <TableCell className="truncate font-mono text-xs text-zinc-400" title={row.intent}>
                  {row.intent}
                </TableCell>
                <TableCell className="truncate font-mono text-xs text-zinc-300" title={row.status}>
                  {row.status}
                </TableCell>
                <TableCell
                  className="truncate font-mono text-xs text-zinc-400"
                  title={localTimestamp(task.updated_at)}
                >
                  {shortTimestamp(task.updated_at)}
                </TableCell>
              </TableRow>
            );
          })
        )}
      </TableBody>
    </Table>
  );
}
