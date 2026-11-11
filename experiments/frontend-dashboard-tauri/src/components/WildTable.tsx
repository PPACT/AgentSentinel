import { Button } from "@/components/ui/button";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import type { WildProcess } from "@/lib/types";
import { isCleanupCandidate } from "@/lib/wild";

interface WildTableProps {
  processes: WildProcess[];
  onCleanup?: (process: WildProcess) => void;
  busyPid?: number | null;
}

const COLUMNS = ["pid", "cwd", "cmdline", "子进程", "操作"];

/** 未注册进程列表：展示进程树根，node/python 残留可强制清理。 */
export function WildTable({ processes, onCleanup, busyPid }: WildTableProps) {
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
        {processes.length === 0 ? (
          <TableRow>
            <TableCell colSpan={COLUMNS.length} className="py-4 text-center text-zinc-500">
              未发现未注册进程
            </TableCell>
          </TableRow>
        ) : (
          processes.map((process) => {
            const pid = process.pid ?? "—";
            const cwd = process.cwd ?? "";
            const cmdline = process.cmdline ?? "";
            const children = process.child_pids?.length ?? 0;
            const cleanable = onCleanup !== undefined && isCleanupCandidate(process);
            const busy = process.pid !== undefined && busyPid === process.pid;
            return (
              <TableRow key={`${pid}-${process.create_time ?? 0}`}>
                <TableCell className="font-mono text-zinc-300">{pid}</TableCell>
                <TableCell
                  className="max-w-[10rem] truncate font-mono text-xs text-zinc-400"
                  title={cwd}
                >
                  {cwd || "—"}
                </TableCell>
                <TableCell
                  className="max-w-[22rem] truncate font-mono text-xs text-zinc-300"
                  title={cmdline}
                >
                  {cmdline || "—"}
                </TableCell>
                <TableCell className="font-mono text-xs text-zinc-400">{children}</TableCell>
                <TableCell>
                  {cleanable ? (
                    <Button
                      variant="outline"
                      size="sm"
                      disabled={busy}
                      title="结束该 node/python 进程及其子进程"
                      onClick={() => onCleanup?.(process)}
                    >
                      {busy ? "清理中…" : "强制清理"}
                    </Button>
                  ) : (
                    <span className="whitespace-nowrap text-[10px] text-zinc-500">只读</span>
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
