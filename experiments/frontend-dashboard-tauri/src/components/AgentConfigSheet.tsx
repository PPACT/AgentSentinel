import { open as openDialog } from "@tauri-apps/plugin-dialog";
import { FileText, Loader2, X } from "lucide-react";
import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { Textarea } from "@/components/ui/textarea";
import { getWorkspaceAgent, saveWorkspaceAgent } from "@/lib/backend";
import { errorMessage } from "@/lib/format";

interface AgentConfigSheetProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  workspaceId: number | null;
  /** null = 新建；否则编辑该角色。 */
  role: string | null;
  onSaved: (role: string) => void;
}

const FIELD_CLASS =
  "h-9 w-full rounded-md border border-zinc-800/80 bg-zinc-900/40 px-3 text-[13px] text-zinc-100 outline-none transition-colors placeholder:text-zinc-600 focus:border-sky-500/50 focus:bg-zinc-900/70 focus:ring-2 focus:ring-sky-500/10";

interface AgentFormProps {
  workspaceId: number | null;
  role: string | null;
  onSaved: (role: string) => void;
  onClose: () => void;
}

function AgentForm({ workspaceId, role, onSaved, onClose }: AgentFormProps) {
  const [draftRole, setDraftRole] = useState(role ?? "");
  const [command, setCommand] = useState("");
  const [wake, setWake] = useState("");
  const [wakeFile, setWakeFile] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!role || workspaceId === null) return;
    let cancelled = false;
    void getWorkspaceAgent(workspaceId, role)
      .then(({ agent }) => {
        if (cancelled || !agent) return;
        setCommand(agent.command ?? "");
        if (agent.wake_prompt_type === "file") setWakeFile(agent.wake_prompt_content);
        else setWake(agent.wake_prompt_content);
      })
      .catch((err) => {
        if (!cancelled) setError(errorMessage(err));
      });
    return () => {
      cancelled = true;
    };
  }, [role, workspaceId]);

  const browseWakeFile = async () => {
    try {
      const selected = await openDialog({
        multiple: false,
        directory: false,
        filters: [{ name: "提示词", extensions: ["md", "txt"] }],
      });
      if (typeof selected === "string" && selected.trim()) setWakeFile(selected);
    } catch (err) {
      setError(errorMessage(err));
    }
  };

  const submit = async () => {
    const normalized = draftRole.trim().toLowerCase();
    if (!normalized) {
      setError("请填写角色名，如 reviewer");
      return;
    }
    if (!wake.trim() && !wakeFile.trim() && !command.trim()) {
      setError("启动命令和唤醒词至少填写一项");
      return;
    }
    if (workspaceId === null) {
      setError("请先保存工作区");
      return;
    }
    setSaving(true);
    setError(null);
    try {
      await saveWorkspaceAgent({
        workspace_id: workspaceId,
        role: normalized,
        wake_prompt_type: wakeFile.trim() ? "file" : "text",
        wake_prompt_content: wakeFile.trim() ? wakeFile.trim() : wake,
        command: command.trim(),
      });
      onSaved(normalized);
      onClose();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setSaving(false);
    }
  };

  return (
    <>
      <div className="flex-1 space-y-6 overflow-auto px-6 pb-4">
        <div className="space-y-2">
          <label htmlFor="agent-role" className="text-[11px] font-medium text-zinc-400">
            角色名
          </label>
          <Input
            id="agent-role"
            value={draftRole}
            disabled={Boolean(role)}
            placeholder="reviewer"
            className={FIELD_CLASS}
            onChange={(event) => setDraftRole(event.target.value)}
          />
          <p className="text-[11px] text-zinc-600">每个角色最多一个 Agent；重名会覆盖配置。</p>
        </div>

        <div className="space-y-2">
          <label htmlFor="agent-command" className="text-[11px] font-medium text-zinc-400">
            启动命令
          </label>
          <Input
            id="agent-command"
            value={command}
            placeholder="留空继承工作区默认，如 claude --model sonnet"
            className={FIELD_CLASS}
            onChange={(event) => setCommand(event.target.value)}
          />
        </div>

        <div className="space-y-2">
          <div className="flex items-center justify-between">
            <label htmlFor="agent-wake" className="text-[11px] font-medium text-zinc-400">
              专属唤醒词
            </label>
            <Button
              type="button"
              variant="ghost"
              size="sm"
              className="h-6 gap-1 px-1.5 text-[11px] text-zinc-400 hover:text-zinc-200"
              onClick={() => void browseWakeFile()}
            >
              <FileText className="size-3.5" />
              浏览文件
            </Button>
          </div>
          {wakeFile ? (
            <div className="flex items-center gap-2 rounded-md border border-zinc-800/80 bg-zinc-900/40 px-3 py-2">
              <FileText className="size-3.5 shrink-0 text-sky-400/80" />
              <span className="min-w-0 flex-1 truncate font-mono text-[11px] text-zinc-300">
                {wakeFile}
              </span>
              <button
                type="button"
                aria-label="清除文件"
                className="rounded p-0.5 text-zinc-500 hover:text-zinc-200"
                onClick={() => setWakeFile("")}
              >
                <X className="size-3.5" />
              </button>
            </div>
          ) : (
            <Textarea
              id="agent-wake"
              value={wake}
              placeholder="例如：你是 reviewer，只做代码审查，先读交接表再给结论。"
              className="min-h-32 border-zinc-800/80 bg-zinc-900/40 text-[13px] focus:border-sky-500/50 focus:ring-2 focus:ring-sky-500/10"
              onChange={(event) => setWake(event.target.value)}
            />
          )}
        </div>

        {error ? <p className="text-[12px] text-red-400">{error}</p> : null}
      </div>

      <SheetFooter>
        <Button
          variant="ghost"
          size="sm"
          className="h-8 px-3 text-xs text-zinc-400"
          onClick={onClose}
        >
          取消
        </Button>
        <Button
          size="sm"
          className="h-8 gap-1.5 bg-sky-500/90 px-3 text-xs text-zinc-950 hover:bg-sky-400"
          disabled={saving}
          onClick={() => void submit()}
        >
          {saving ? <Loader2 className="size-3.5 animate-spin" /> : null}
          保存角色
        </Button>
      </SheetFooter>
    </>
  );
}

/** 右侧 Agent 配置抽屉：角色、启动命令、唤醒词（文本或文件）。 */
export function AgentConfigSheet({
  open,
  onOpenChange,
  workspaceId,
  role,
  onSaved,
}: AgentConfigSheetProps) {
  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent>
        <SheetHeader>
          <SheetTitle>{role ? `编辑 ${role}` : "新建 Agent"}</SheetTitle>
          <SheetDescription>
            角色使用工作区引擎启动；命令留空则继承工作区默认命令。唤醒词只在该角色首次启动时注入。
          </SheetDescription>
        </SheetHeader>
        {open ? (
          <AgentForm
            key={`${role ?? "new"}`}
            workspaceId={workspaceId}
            role={role}
            onSaved={onSaved}
            onClose={() => onOpenChange(false)}
          />
        ) : null}
      </SheetContent>
    </Sheet>
  );
}
