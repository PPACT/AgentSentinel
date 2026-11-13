import { FolderCog, Plus, RefreshCw, Trash2 } from "lucide-react";
import { useState, type ReactNode } from "react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import {
  activateWorkspace,
  deleteWorkspace,
  preflightWorkspace,
  saveWorkspace,
} from "@/lib/backend";
import { errorMessage } from "@/lib/format";
import type {
  EngineOption,
  PreflightResult,
  Workspace,
  WorkspacesResponse,
} from "@/lib/types";

interface WorkspaceDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  workspaces: Workspace[];
  engines: EngineOption[];
  activeId: number | null;
  onRefresh: (data: WorkspacesResponse) => void;
}

interface FormState {
  id: number | null;
  name: string;
  project_path: string;
  engine: string;
  command: string;
  wakeMode: "text" | "file";
  wake_prompt: string;
  wake_prompt_file: string;
  budget_tokens: number;
}

const EMPTY_FORM: FormState = {
  id: null,
  name: "",
  project_path: "",
  engine: "claude",
  command: "claude",
  wakeMode: "text",
  wake_prompt: "",
  wake_prompt_file: "",
  budget_tokens: 200_000,
};

function fromWorkspace(workspace: Workspace | undefined): FormState {
  if (!workspace) return { ...EMPTY_FORM };
  return {
    id: workspace.id,
    name: workspace.name,
    project_path: workspace.project_path,
    engine: workspace.engine,
    command: workspace.command,
    wakeMode: workspace.wake_prompt_file ? "file" : "text",
    wake_prompt: workspace.wake_prompt,
    wake_prompt_file: workspace.wake_prompt_file,
    budget_tokens: workspace.budget_tokens,
  };
}

function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: ReactNode;
}) {
  return (
    <label className="block space-y-1">
      <span className="text-[11px] text-zinc-400">{label}</span>
      {children}
      {hint ? <span className="block text-[10px] text-zinc-600">{hint}</span> : null}
    </label>
  );
}

/** 工作区配置中心：项目路径 / 启动命令 / 唤醒提示词 / 预算，配置存中控 SQLite。 */
export function WorkspaceDialog({
  open,
  onOpenChange,
  workspaces,
  engines,
  activeId,
  onRefresh,
}: WorkspaceDialogProps) {
  const initial = workspaces.find((workspace) => workspace.id === activeId) ?? workspaces[0];
  const [selectedId, setSelectedId] = useState<number | null>(initial?.id ?? null);
  const [form, setForm] = useState<FormState>(fromWorkspace(initial));
  const [preflight, setPreflight] = useState<PreflightResult | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const selectWorkspace = (workspace: Workspace) => {
    setSelectedId(workspace.id);
    setForm(fromWorkspace(workspace));
    setPreflight(workspace.preflight ?? null);
    setMessage(null);
  };

  const startNew = () => {
    setSelectedId(null);
    setForm({ ...EMPTY_FORM });
    setPreflight(null);
    setMessage(null);
  };

  const selectEngine = (engineId: string) => {
    const option = engines.find((engine) => engine.id === engineId);
    setForm((previous) => {
      const previousDefault =
        engines.find((engine) => engine.id === previous.engine)?.default_command ?? "";
      const command =
        !previous.command || previous.command === previousDefault
          ? (option?.default_command ?? previous.command)
          : previous.command;
      return { ...previous, engine: engineId, command };
    });
  };

  const runPreflight = async () => {
    setBusy(true);
    setMessage(null);
    try {
      setPreflight(await preflightWorkspace(form.command, form.project_path));
    } catch (error) {
      setMessage(errorMessage(error));
    } finally {
      setBusy(false);
    }
  };

  const save = async () => {
    setBusy(true);
    setMessage(null);
    try {
      const payload = {
        ...form,
        wake_prompt: form.wakeMode === "text" ? form.wake_prompt : "",
        wake_prompt_file: form.wakeMode === "file" ? form.wake_prompt_file : "",
        activate: form.id === null,
      };
      const data = await saveWorkspace(payload);
      onRefresh(data);
      if (data.workspace) {
        setSelectedId(data.workspace.id);
        setForm(fromWorkspace(data.workspace));
        setPreflight(data.workspace.preflight ?? null);
      }
      const check = data.workspace?.preflight;
      setMessage(
        check && !check.ok ? `已保存，但启动预检未通过：${check.error ?? ""}` : "已保存",
      );
    } catch (error) {
      setMessage(errorMessage(error));
    } finally {
      setBusy(false);
    }
  };

  const activate = async () => {
    if (form.id === null) return;
    setBusy(true);
    setMessage(null);
    try {
      const data = await activateWorkspace(form.id);
      onRefresh(data);
      setPreflight(data.workspace?.preflight ?? null);
      setMessage("已设为当前工作区");
    } catch (error) {
      setMessage(errorMessage(error));
    } finally {
      setBusy(false);
    }
  };

  const remove = async () => {
    if (form.id === null) return;
    if (!window.confirm(`删除工作区「${form.name}」？配置会被移除，不会删除项目文件。`)) return;
    setBusy(true);
    setMessage(null);
    try {
      const data = await deleteWorkspace(form.id);
      onRefresh(data);
      const next =
        data.workspaces.find((workspace) => workspace.id === data.active_id) ?? data.workspaces[0];
      setSelectedId(next?.id ?? null);
      setForm(fromWorkspace(next));
      setPreflight(next?.preflight ?? null);
      setMessage("已删除");
    } catch (error) {
      setMessage(errorMessage(error));
    } finally {
      setBusy(false);
    }
  };

  const selectedEngine = engines.find((engine) => engine.id === form.engine);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[85vh] overflow-hidden border-zinc-800 bg-zinc-950 text-zinc-200 sm:max-w-4xl">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2 font-mono text-sm">
            <FolderCog className="size-4 text-sky-400" />
            设置 / 工作区
          </DialogTitle>
          <DialogDescription>
            配置保存在中控本地 SQLite，不写入项目文件；切换工作区时注册表、任务与终端会话相互隔离。
          </DialogDescription>
        </DialogHeader>

        <div className="grid min-h-0 grid-cols-[220px_minmax(0,1fr)] gap-4">
          <div className="flex min-h-0 flex-col gap-2 border-r border-zinc-800 pr-3">
            <Button
              size="sm"
              variant="outline"
              className="h-7 justify-start gap-1.5 border-zinc-700 text-xs"
              onClick={startNew}
            >
              <Plus className="size-3.5" />
              新建工作区
            </Button>
            <div className="min-h-0 flex-1 space-y-1 overflow-y-auto">
              {workspaces.map((workspace) => (
                <button
                  key={workspace.id}
                  type="button"
                  onClick={() => selectWorkspace(workspace)}
                  className={`w-full rounded px-2 py-1.5 text-left text-xs transition-colors ${
                    selectedId === workspace.id
                      ? "bg-zinc-800 text-zinc-100"
                      : "text-zinc-400 hover:bg-zinc-800/50"
                  }`}
                >
                  <span className="flex items-center gap-1.5">
                    <span className="truncate font-medium">{workspace.name}</span>
                    {workspace.id === activeId ? (
                      <span className="rounded bg-emerald-950/60 px-1 text-[9px] text-emerald-400">
                        当前
                      </span>
                    ) : null}
                  </span>
                  <span className="mt-0.5 block truncate font-mono text-[9px] text-zinc-600">
                    {workspace.project_path}
                  </span>
                </button>
              ))}
            </div>
          </div>

          <div className="min-h-0 space-y-3 overflow-y-auto pr-1">
            <div className="grid grid-cols-2 gap-3">
              <Field label="名称">
                <Input
                  value={form.name}
                  placeholder="例如 AgentSentinel"
                  onChange={(event) => setForm({ ...form, name: event.target.value })}
                />
              </Field>
              <Field label="项目路径" hint="中控拉起 Agent 时直接切换到此 cwd">
                <Input
                  value={form.project_path}
                  placeholder="D:\\Code\\your-project"
                  onChange={(event) => setForm({ ...form, project_path: event.target.value })}
                />
              </Field>
            </div>

            <div className="grid grid-cols-2 gap-3">
              <Field label="引擎预设">
                <select
                  value={form.engine}
                  onChange={(event) => selectEngine(event.target.value)}
                  className="h-9 w-full rounded-md border border-zinc-700 bg-zinc-900 px-2 text-xs text-zinc-200 outline-none focus:border-sky-700"
                >
                  {engines.map((engine) => (
                    <option key={engine.id} value={engine.id}>
                      {engine.label}
                      {engine.chat ? "" : "（仅终端）"}
                    </option>
                  ))}
                </select>
              </Field>
              <Field
                label="启动命令"
                hint="请确保该命令已在本机终端中可正常执行；可改为自定义 CLI"
              >
                <Input
                  value={form.command}
                  placeholder={selectedEngine?.default_command || "例如 codex / my-cli"}
                  onChange={(event) => setForm({ ...form, command: event.target.value })}
                />
              </Field>
            </div>

            <Field label="唤醒提示词">
              <div className="mb-1 flex items-center gap-3 text-[11px] text-zinc-400">
                <label className="flex items-center gap-1">
                  <input
                    type="radio"
                    checked={form.wakeMode === "text"}
                    onChange={() => setForm({ ...form, wakeMode: "text" })}
                  />
                  直接输入
                </label>
                <label className="flex items-center gap-1">
                  <input
                    type="radio"
                    checked={form.wakeMode === "file"}
                    onChange={() => setForm({ ...form, wakeMode: "file" })}
                  />
                  读取 .md 文件
                </label>
              </div>
              {form.wakeMode === "text" ? (
                <Textarea
                  value={form.wake_prompt}
                  placeholder="粘贴你的唤醒词 / 交接提示词…"
                  className="min-h-24"
                  onChange={(event) => setForm({ ...form, wake_prompt: event.target.value })}
                />
              ) : (
                <Input
                  value={form.wake_prompt_file}
                  placeholder="D:\\path\\wake.md"
                  onChange={(event) => setForm({ ...form, wake_prompt_file: event.target.value })}
                />
              )}
            </Field>

            <div className="grid grid-cols-2 gap-3">
              <Field label="会话预算（tokens）" hint="用于对话页 Token 进度条">
                <Input
                  type="number"
                  min={1}
                  value={form.budget_tokens}
                  onChange={(event) =>
                    setForm({ ...form, budget_tokens: Number(event.target.value) || 0 })
                  }
                />
              </Field>
              <div className="flex items-end gap-2">
                <Button
                  size="sm"
                  variant="outline"
                  className="h-8 gap-1.5 text-xs"
                  disabled={busy}
                  onClick={() => void runPreflight()}
                >
                  <RefreshCw className="size-3.5" />
                  检测环境
                </Button>
                {preflight ? (
                  <span
                    className={`text-[11px] ${preflight.ok ? "text-emerald-400" : "text-red-400"}`}
                  >
                    {preflight.ok ? "命令与路径可用" : preflight.error}
                  </span>
                ) : null}
              </div>
            </div>

            <div className="flex items-center justify-between border-t border-zinc-800 pt-3">
              <div className="flex gap-2">
                <Button size="sm" className="h-8 text-xs" disabled={busy} onClick={() => void save()}>
                  保存
                </Button>
                <Button
                  size="sm"
                  variant="outline"
                  className="h-8 border-zinc-700 text-xs"
                  disabled={busy || form.id === null || form.id === activeId}
                  onClick={() => void activate()}
                >
                  设为当前
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  className="h-8 gap-1.5 text-xs text-red-300 hover:text-red-200"
                  disabled={busy || form.id === null}
                  onClick={() => void remove()}
                >
                  <Trash2 className="size-3.5" />
                  删除
                </Button>
              </div>
              {message ? <span className="text-[11px] text-zinc-400">{message}</span> : null}
            </div>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}
