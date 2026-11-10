import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { localTimestamp } from "@/lib/format";
import type { Task } from "@/lib/types";

interface TaskDetailsDialogProps {
  task: Task | null;
  onOpenChange: (open: boolean) => void;
}

function display(value: unknown): string {
  if (value === null || value === undefined || value === "") return "—";
  return String(value);
}

function Field({ label, value, mono = false }: { label: string; value: unknown; mono?: boolean }) {
  return (
    <div className="min-w-0">
      <div className="text-zinc-500">{label}</div>
      <div className={`break-all ${mono ? "font-mono text-zinc-300" : "text-zinc-200"}`}>
        {display(value)}
      </div>
    </div>
  );
}

function ListField({ label, items }: { label: string; items?: string[] }) {
  return (
    <div className="min-w-0 sm:col-span-2">
      <div className="text-zinc-500">{label}</div>
      {items && items.length > 0 ? (
        <ul className="list-disc pl-4 font-mono text-zinc-300">
          {items.map((item, index) => (
            <li key={`${index}-${item}`} className="break-all">
              {item}
            </li>
          ))}
        </ul>
      ) : (
        <div className="text-zinc-300">—</div>
      )}
    </div>
  );
}

/** 任务详情：点击 Tasks 行打开，展示字段、配额与时间线（本地时间）。 */
export function TaskDetailsDialog({ task, onOpenChange }: TaskDetailsDialogProps) {
  return (
    <Dialog open={task !== null} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[80vh] overflow-auto border-zinc-800 bg-zinc-950 text-zinc-200 sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle className="font-mono text-sm">
            {task ? display(task.task_id) : "任务详情"}
          </DialogTitle>
          <DialogDescription>任务字段与时间线（本地时间）</DialogDescription>
        </DialogHeader>
        {task ? (
          <div className="grid grid-cols-1 gap-x-6 gap-y-3 text-xs sm:grid-cols-2">
            <Field label="status" value={task.status} mono />
            <Field
              label="from → to"
              value={`${display(task.from_role)} → ${display(task.to_role)}`}
              mono
            />
            <Field label="intent" value={task.intent} mono />
            <Field label="pointer" value={task.pointer} mono />
            <Field label="idempotency_key" value={task.idempotency_key} mono />
            <Field label="attempts" value={task.attempts} />
            <Field label="max_tokens" value={task.max_tokens} />
            <Field label="max_minutes" value={task.max_minutes} />
            <Field label="cost_tokens" value={task.cost_tokens} />
            <Field label="created_at" value={localTimestamp(task.created_at)} mono />
            <Field label="updated_at" value={localTimestamp(task.updated_at)} mono />
            <Field label="ack_at" value={localTimestamp(task.ack_at)} mono />
            <Field label="completed_at" value={localTimestamp(task.completed_at)} mono />
            <ListField label="acceptance" items={task.acceptance} />
            <ListField label="artifacts" items={task.artifacts} />
          </div>
        ) : null}
      </DialogContent>
    </Dialog>
  );
}
