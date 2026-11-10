import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { formatTask, localTimestamp } from "@/lib/format";
import type { Task } from "@/lib/types";

interface TaskTableProps {
  tasks: Task[];
  onSelect?: (task: Task) => void;
}

/** 右侧 Tasks 栏较窄：固定列宽 + 截断，避免横向溢出。 */
const COLUMNS = [
  { key: "task_id", label: "task_id", width: "w-[32%]" },
  { key: "from_role", label: "from_role", width: "w-[11%]" },
  { key: "to_role", label: "to_role", width: "w-[11%]" },
  { key: "intent", label: "intent", width: "w-[16%]" },
  { key: "status", label: "status", width: "w-[13%]" },
  { key: "updated_at", label: "updated_at", width: "w-[17%]" },
] as const;

export function TaskTable({ tasks, onSelect }: TaskTableProps) {
  return (
    <Table className="table-fixed">
      <TableHeader>
        <TableRow>
          {COLUMNS.map((column) => (
            <TableHead key={column.key} className={column.width}>
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
                <TableCell className="truncate text-zinc-300" title={row.fromRole}>
                  {row.fromRole}
                </TableCell>
                <TableCell className="truncate text-zinc-300" title={row.toRole}>
                  {row.toRole}
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
                  {localTimestamp(task.updated_at)}
                </TableCell>
              </TableRow>
            );
          })
        )}
      </TableBody>
    </Table>
  );
}
