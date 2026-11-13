import { open as openDialog } from "@tauri-apps/plugin-dialog";
import { FolderCog, FolderOpen, Plus, RefreshCw, Trash2 } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";

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
  deleteWorkspaceAgent,
  getWorkspaceAgents,
  preflightWorkspace,
  saveWorkspace,
  saveWorkspaceAgent,
} from "@/lib/backend";
import { errorMessage } from "@/lib/format";
import type {
  EngineOption,
  PreflightResult,
  Workspace,
  WorkspaceAgent,
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
}

const EMPTY_FORM: FormState = {
  id: null,
  name: "",
  project_path: "",
  engine: "claude",
  command: "claude",
};

function fromWorkspace(workspace: Workspace | undefined): FormState {
  if (!workspace) return { ...EMPTY_FORM };
  return {
    id: workspace.id,
    name: workspace.name,
    project_path: workspace.project_path,
    engine: workspace.engine,
    command: workspace.command,
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

/** 设置/工作区：Workspace 配置 + 每个 Agent 角色的专属唤醒词。 */
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

  const [agentConfigs, setAgentConfigs] = useState<WorkspaceAgent[]>([]);
  const [agentRole, setAgentRole] = useState("");
  const [agentType, setAgentType] = useState<"text" | "file">("text");
  const [agentContent, setAgentContent] = useState("");
  const [agentMessage, setAgentMessage] = useState<string | null>(null);
  const [agentBusy, setAgentBusy] = useState(false);

  useEffect(() => {
    let cancelled = false;
    if (selectedId === null) return;
    void getWorkspaceAgents(selectedId)
      .then((data) => {
        if (!cancelled) setAgentConfigs(data.agents ?? []);
      })
      .catch(() => {
        if (!cancelled) setAgentConfigs([]);
      });
    return () => {
      cancelled = true;
    };
  }, [selectedId]);

  const selectWorkspace = (workspace: Workspace) => {
    setSelectedId(workspace.id);
    setForm(fromWorkspace(workspace));
    setPreflight(workspace.preflight ?? null);
    setMessage(null);
    setAgentRole("");
    setAgentType("text");
    setAgentContent("");
    setAgentMessage(null);
  };

  const startNew = () => {
    setSelectedId(null);
    setForm({ ...EMPTY_FORM });
    setPreflight(null);
    setMessage(null);
    setAgentConfigs([]);
    setAgentRole("");
    setAgentType("text");
    setAgentContent("");
    setAgentMessage(null);
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

  const browseWakeFile = async () => {
    const selected = await openDialog({
      multiple: false,
      title: "选择唤醒提示词文件",
      filters: [{ name: "Text", extensions: ["md", "txt"] }],
    });
    if (typeof selected === "string") {
      setAgentType("file");
      setAgentContent(selected);
    }
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
      setAgentConfigs([]);
      setAgentRole("");
      setAgentContent("");
      setMessage("已删除");
    } catch (error) {
      setMessage(errorMessage(error));
    } finally {
      setBusy(false);
    }
  };

  const selectAgent = (agent: WorkspaceAgent) => {
    setAgentRole(agent.role);
    setAgentType(agent.wake_prompt_type === "file" ? "file" : "text");
    setAgentContent(agent.wake_prompt_content);
    setAgentMessage(null);
  };

  const resetAgent = () => {
    setAgentRole("");
    setAgentType("text");
    setAgentContent("");
    setAgentMessage(null);
  };

  const saveAgent = async () => {
    if (selectedId === null) {
      setAgentMessage("请先保存工作区，再配置 Agent 唤醒词");
      return;
    }
    const role = agentRole.trim().toLowerCase();
    if (!role) {
      setAgentMessage("请填写角色名（如 dev / doc / local）");
      return;
    }
    if (!agentContent.trim()) {
      setAgentMessage("唤醒词内容不能为空");
      return;
    }
    setAgentBusy(true);
    setAgentMessage(null);
    try {
      const data = await saveWorkspaceAgent({
        workspace_id: selectedId,
        role,
        wake_prompt_type: agentType,
        wake_prompt_content: agentContent,
      });
      setAgentConfigs(data.agents ?? []);
      setAgentRole(role);
      setAgentMessage(`已保存 ${role} 的唤醒词`);
    } catch (error) {
      setAgentMessage(errorMessage(error));
    } finally {
      setAgentBusy(false);
    }
  };

  const deleteAgent = async () => {
    if (selectedId === null || !agentRole) return;
    if (!window.confirm(`删除 ${agentRole} 的唤醒词配置？`)) return;
    setAgentBusy(true);
    setAgentMessage(null);
    try {
      const data = await deleteWorkspaceAgent(selectedId, agentRole);
      setAgentConfigs(data.agents ?? []);
      resetAgent();
      setAgentMessage("已删除该角色的唤醒词");
    } catch (error) {
      setAgentMessage(errorMessage(error));
    } finally {
      setAgentBusy(false);
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
            配置保存在中控本地 SQLite；唤醒词按 Agent 角色独立配置，交接时按 workspace + role 取词。
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
                <div className="flex gap-1.5">
                  <Input
                    value={form.project_path}
                    placeholder="请选择或输入项目路径"
                    onChange={(event) => setForm({ ...form, project_path: event.target.value })}
                  />
                  <Button
                    type="button"
                    size="sm"
                    variant="outline"
                    className="h-9 shrink-0 gap-1 border-zinc-700 px-2 text-[11px]"
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

            <div className="border-t border-zinc-800 pt-3">
              <div className="mb-2 flex items-center gap-2 text-xs font-medium text-zinc-200">
                Agent 唤醒词
                <span className="text-[10px] font-normal text-zinc-500">
                  每个角色独立；一键交接时按 workspace + role 取词
                </span>
              </div>
              <div className="mb-2 flex flex-wrap items-center gap-1">
                {agentConfigs.map((agent) => (
                  <button
                    key={agent.role}
                    type="button"
                    onClick={() => selectAgent(agent)}
                    className={`rounded border px-1.5 py-0.5 text-[10px] transition-colors ${
                      agentRole === agent.role
                        ? "border-sky-700 bg-sky-950/40 text-sky-300"
                        : "border-zinc-700 text-zinc-400 hover:bg-zinc-800/50"
                    }`}
                  >
                    {agent.role}
                    {agent.configured ? "" : "（空）"}
                  </button>
                ))}
                <button
                  type="button"
                  onClick={resetAgent}
                  className="flex items-center gap-0.5 rounded border border-dashed border-zinc-700 px-1.5 py-0.5 text-[10px] text-zinc-500 hover:text-zinc-300"
                >
                  <Plus className="size-3" />
                  新建角色
                </button>
              </div>
              <div className="grid grid-cols-[1fr_180px] gap-2">
                <Field label="角色">
                  <Input
                    value={agentRole}
                    placeholder="dev / doc / local"
                    onChange={(event) => setAgentRole(event.target.value)}
                  />
                </Field>
                <Field label="提示词来源">
                  <select
                    value={agentType}
                    onChange={(event) =>
                      setAgentType(event.target.value === "file" ? "file" : "text")
                    }
                    className="h-9 w-full rounded-md border border-zinc-700 bg-zinc-900 px-2 text-xs text-zinc-200 outline-none focus:border-sky-700"
                  >
                    <option value="text">直接输入</option>
                    <option value="file">读取 .md / .txt 文件</option>
                  </select>
                </Field>
              </div>
              {agentType === "text" ? (
                <Textarea
                  value={agentContent}
                  placeholder="该 Agent 专属唤醒词；交接 /clear 后注入…"
                  className="mt-2 min-h-20"
                  onChange={(event) => setAgentContent(event.target.value)}
                />
              ) : (
                <div className="mt-2 flex gap-1.5">
                  <Input
                    value={agentContent}
                    placeholder="请选择或输入提示词的文件路径"
                    onChange={(event) => setAgentContent(event.target.value)}
                  />
                  <Button
                    type="button"
                    size="sm"
                    variant="outline"
                    className="h-9 shrink-0 gap-1 border-zinc-700 px-2 text-[11px]"
                    onClick={() => void browseWakeFile()}
                  >
                    <FolderOpen className="size-3.5" />
                    浏览
                  </Button>
                </div>
              )}
              <div className="mt-2 flex items-center gap-2">
                <Button
                  size="sm"
                  className="h-8 text-xs"
                  disabled={agentBusy || selectedId === null}
                  onClick={() => void saveAgent()}
                >
                  保存角色唤醒词
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  className="h-8 gap-1 text-xs text-red-300 hover:text-red-200"
                  disabled={agentBusy || selectedId === null || !agentRole}
                  onClick={() => void deleteAgent()}
                >
                  <Trash2 className="size-3.5" />
                  删除
                </Button>
                {agentMessage ? (
                  <span className="text-[11px] text-zinc-400">{agentMessage}</span>
                ) : null}
              </div>
            </div>

            <div className="flex items-center justify-between border-t border-zinc-800 pt-3">
              <div className="flex items-center gap-2">
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
                <Button
                  size="sm"
                  variant="outline"
                  className="h-8 gap-1.5 border-zinc-700 text-xs"
                  disabled={busy}
                  onClick={() => void runPreflight()}
                >
                  <RefreshCw className="size-3.5" />
                  检测环境
                </Button>
              </div>
              <div className="flex items-center gap-2">
                {preflight ? (
                  <span
                    className={`text-[11px] ${preflight.ok ? "text-emerald-400" : "text-red-400"}`}
                  >
                    {preflight.ok ? "命令与路径可用" : preflight.error}
                  </span>
                ) : null}
                {message ? <span className="text-[11px] text-zinc-400">{message}</span> : null}
              </div>
            </div>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}
