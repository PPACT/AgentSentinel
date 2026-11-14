import { open as openDialog } from "@tauri-apps/plugin-dialog";
import { FolderCog, FolderOpen, Plus } from "lucide-react";
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
  budget_tokens: number;
}

const EMPTY_FORM: FormState = {
  id: null,
  name: "",
  project_path: "",
  engine: "claude",
  command: "claude",
  budget_tokens: 200000,
};

function fromWorkspace(workspace: Workspace | undefined): FormState {
  if (!workspace) return { ...EMPTY_FORM };
  return {
    id: workspace.id,
    name: workspace.name,
    project_path: workspace.project_path,
    engine: workspace.engine,
    command: workspace.command,
    budget_tokens: workspace.budget_tokens ?? EMPTY_FORM.budget_tokens,
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
    <label className="block space-y-1.5">
      <span className="text-[11px] font-medium text-zinc-400">{label}</span>
      {children}
      {hint ? <span className="block text-[10px] leading-4 text-zinc-600">{hint}</span> : null}
    </label>
  );
}

const FIELD_CLASS =
  "h-9 w-full rounded-md border border-zinc-800/80 bg-zinc-900/40 px-3 text-[13px] text-zinc-100 outline-none transition-colors placeholder:text-zinc-600 focus:border-sky-500/50 focus:bg-zinc-900/70 focus:ring-2 focus:ring-sky-500/10";

/** 设置 / 工作区：只保留环境配置（Agent 配置在右侧 Sheet）。 */
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

  const browseProject = async () => {
    const selected = await openDialog({
      directory: true,
      multiple: false,
      title: "选择项目目录",
    });
    if (typeof selected === "string") setForm((previous) => ({ ...previous, project_path: selected }));
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
      const data = await saveWorkspace({ ...form, activate: form.id === null });
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
      <DialogContent className="max-h-[82vh] overflow-hidden border-zinc-800/80 bg-zinc-950 text-zinc-200 sm:max-w-3xl">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2 text-sm font-medium tracking-tight">
            <FolderCog className="size-4 text-sky-400/80" />
            设置 / 工作区
          </DialogTitle>
          <DialogDescription>
            环境配置保存在中控本地 SQLite；Agent 角色与唤醒词已移到左侧 + 菜单。
          </DialogDescription>
        </DialogHeader>

        <div className="grid min-h-0 grid-cols-[200px_minmax(0,1fr)] gap-5">
          <div className="flex min-h-0 flex-col gap-1.5 border-r border-zinc-900 pr-4">
            <Button
              size="sm"
              variant="ghost"
              className="h-7 justify-start gap-1.5 px-2 text-[12px] text-zinc-400 hover:text-zinc-100"
              onClick={startNew}
            >
              <Plus className="size-3.5" />
              新建工作区
            </Button>
            <div className="mt-1 min-h-0 flex-1 space-y-0.5 overflow-y-auto">
              {workspaces.map((workspace) => (
                <button
                  key={workspace.id}
                  type="button"
                  onClick={() => selectWorkspace(workspace)}
                  className={`w-full rounded-md px-2 py-1.5 text-left text-[12px] transition-colors ${
                    selectedId === workspace.id
                      ? "bg-zinc-900 text-zinc-100 ring-1 ring-inset ring-zinc-800"
                      : "text-zinc-500 hover:bg-zinc-900/60 hover:text-zinc-300"
                  }`}
                >
                  <span className="flex items-center gap-1.5">
                    <span className="truncate font-medium">{workspace.name}</span>
                    {workspace.id === activeId ? (
                      <span className="rounded bg-emerald-500/10 px-1 text-[9px] text-emerald-400">
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

          <div className="min-h-0 space-y-4 overflow-y-auto pr-1">
            <div className="grid grid-cols-2 gap-3">
              <Field label="项目名称">
                <Input
                  value={form.name}
                  className={FIELD_CLASS}
                  placeholder="例如 AgentSentinel"
                  onChange={(event) => setForm({ ...form, name: event.target.value })}
                />
              </Field>
              <Field label="项目路径" hint="中控拉起 Agent 时切换到此 cwd">
                <div className="flex gap-1.5">
                  <Input
                    value={form.project_path}
                    className={FIELD_CLASS}
                    placeholder="请选择或输入项目路径"
                    onChange={(event) => setForm({ ...form, project_path: event.target.value })}
                  />
                  <Button
                    type="button"
                    size="sm"
                    variant="outline"
                    className="h-9 shrink-0 gap-1 border-zinc-800 px-2 text-[11px] text-zinc-300"
                    onClick={() => void browseProject()}
                  >
                    <FolderOpen className="size-3.5" />
                    浏览
                  </Button>
                </div>
              </Field>
            </div>

            <div className="grid grid-cols-2 gap-3">
              <Field label="引擎预设">
                <select
                  value={form.engine}
                  onChange={(event) => selectEngine(event.target.value)}
                  className={FIELD_CLASS}
                >
                  {engines.map((engine) => (
                    <option key={engine.id} value={engine.id}>
                      {engine.label}
                      {engine.chat ? "" : "（仅终端）"}
                    </option>
                  ))}
                </select>
              </Field>
              <Field label="默认启动命令" hint="角色命令留空时继承此命令">
                <Input
                  value={form.command}
                  className={FIELD_CLASS}
                  placeholder={selectedEngine?.default_command || "例如 codex / my-cli"}
                  onChange={(event) => setForm({ ...form, command: event.target.value })}
                />
              </Field>
            </div>

            <Field label="会话预算" hint="对话页 Token 进度条按此预算计算">
              <Input
                type="number"
                min={1}
                step={1000}
                value={form.budget_tokens}
                className={FIELD_CLASS}
                onChange={(event) =>
                  setForm({
                    ...form,
                    budget_tokens: Math.max(1, Number(event.target.value) || 1),
                  })
                }
              />
            </Field>

            <div className="flex items-center gap-2 pt-1">
              <Button
                size="sm"
                className="h-8 bg-sky-500/90 px-3 text-xs text-zinc-950 hover:bg-sky-400"
                disabled={busy}
                onClick={() => void save()}
              >
                保存工作区
              </Button>
              <Button
                size="sm"
                variant="ghost"
                className="h-8 px-2 text-xs text-zinc-400"
                disabled={busy}
                onClick={() => void runPreflight()}
              >
                检测环境
              </Button>
              <Button
                size="sm"
                variant="ghost"
                className="h-8 px-2 text-xs text-zinc-400"
                disabled={busy || form.id === null || form.id === activeId}
                onClick={() => void activate()}
              >
                设为当前
              </Button>
              <Button
                size="sm"
                variant="ghost"
                className="h-8 px-2 text-xs text-red-400 hover:text-red-300"
                disabled={busy || form.id === null}
                onClick={() => void remove()}
              >
                删除
              </Button>
              {preflight ? (
                <span
                  className={`text-[11px] ${preflight.ok ? "text-emerald-400" : "text-red-400"}`}
                >
                  {preflight.ok ? "命令与路径可用" : preflight.error}
                </span>
              ) : null}
              {message ? <span className="text-[11px] text-zinc-500">{message}</span> : null}
            </div>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}
