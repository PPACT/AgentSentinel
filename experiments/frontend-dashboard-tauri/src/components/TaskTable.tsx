import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { formatTask } from "@/lib/format";
import type { Task } from "@/lib/types";

interface TaskTableProps {
  tasks: Task[];
}

const COLUMNS = ["task_id", "from_role", "to_role", "intent", "status", "updated_at"];

export function TaskTable({ tasks }: TaskTableProps) {
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
              <TableRow key={`${row.taskId}-${row.updatedAt}`}>
                <TableCell className="font-mono text-xs text-zinc-200">{row.taskId}</TableCell>
                <TableCell className="text-zinc-300">{row.fromRole}</TableCell>
                <TableCell className="text-zinc-300">{row.toRole}</TableCell>
                <TableCell className="font-mono text-xs text-zinc-400">{row.intent}</TableCell>
                <TableCell className="font-mono text-xs text-zinc-300">{row.status}</TableCell>
                <TableCell className="font-mono text-xs text-zinc-400">{row.updatedAt}</TableCell>
              </TableRow>
            );
          })
        )}
      </TableBody>
    </Table>
  );
}
