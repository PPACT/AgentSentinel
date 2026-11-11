import { TaskTable } from "@/components/TaskTable";
import type { Task } from "@/lib/types";

interface TasksPanelProps {
  tasks: Task[];
  onSelect: (task: Task) => void;
}

/** 右侧 Tasks 栏：只保留任务名/流转/状态/更新时间，细节进 tooltip。 */
export function TasksPanel({ tasks, onSelect }: TasksPanelProps) {
  return (
    <section className="flex h-full min-h-0 min-w-0 flex-col p-2.5">
      <h2 className="mb-2 text-[11px] font-semibold uppercase tracking-wider text-sky-400">Tasks</h2>
      <div className="min-h-0 flex-1 overflow-auto rounded-md border border-zinc-800">
        <TaskTable tasks={tasks} onSelect={onSelect} />
      </div>
    </section>
  );
}
