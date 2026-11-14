import { Pencil, Square, Trash2 } from "lucide-react";

import { formatAgent, localTimestamp, shortProject } from "@/lib/format";
import type { Agent } from "@/lib/types";

interface AgentTableProps {
  agents: Agent[];
  pendingRole: string | null;
  onStop: (role: string) => void;
  onSelect?: (role: string) => void;
  selectedRole?: string | null;
  onEdit?: (role: string) => void;
  onDelete?: (role: string) => void;
}

const STATUS_LABEL: Record<string, string> = {
  registered: "在线",
  running: "运行",
  stopped: "已停止",
  lost: "失联",
  pending: "待注册",
};

function sourceOf(agent: Agent): "local" | "managed" | "external" {
  if (agent.source === "local") return Boolean(agent.job_id) ? "managed" : "local";
  if (agent.source === "external") return "external";
  if (agent.command === "workspace") return "local";
  if (agent.job_id) return "managed";
  return "external";
}

const AVATAR_TONE = [
  "border-sky-500/25 bg-gradient-to-br from-sky-500/20 to-cyan-500/5 text-sky-200",
  "border-emerald-500/25 bg-gradient-to-br from-emerald-500/20 to-teal-500/5 text-emerald-200",
  "border-amber-500/25 bg-gradient-to-br from-amber-500/20 to-orange-500/5 text-amber-200",
  "border-rose-500/25 bg-gradient-to-br from-rose-500/20 to-pink-500/5 text-rose-200",
  "border-indigo-500/25 bg-gradient-to-br from-indigo-500/20 to-sky-500/5 text-indigo-200",
];

function avatarTone(role: string): string {
  let hash = 0;
  for (const char of role) hash = (hash * 31 + char.charCodeAt(0)) % 997;
  return AVATAR_TONE[hash % AVATAR_TONE.length];
}

/** DEV / DOC / REV：多段角色取首字母，单段取前三位。 */
function initials(role: string): string {
  const parts = role.split(/[-_\s]+/).filter(Boolean);
  if (parts.length >= 2) return parts.slice(0, 3).map((part) => part[0]).join("").toUpperCase();
  return role.slice(0, 3).toUpperCase();
}

/** 左侧 Agent 列表：头像 + 角色 + 状态灯；编辑/删除仅在悬停时浮现。 */
export function AgentTable({
  agents,
  pendingRole,
  onStop,
  onSelect,
  selectedRole,
  onEdit,
  onDelete,
}: AgentTableProps) {
  if (agents.length === 0) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-1 px-4 text-center">
        <p className="text-[12px] text-zinc-500">还没有 Agent</p>
        <p className="text-[11px] text-zinc-600">点上方 + 新建角色或本地终端</p>
      </div>
    );
  }

  return (
    <ul className="flex flex-col gap-0.5 p-1">
      {agents.map((agent) => {
        const row = formatAgent(agent);
        const role = agent.role ?? "";
        const source = sourceOf(agent);
        const pending = pendingRole === role;
        const selected = selectedRole === role && Boolean(role);
        const statusLabel =
          agent.alive === false && row.status !== "stopped"
            ? "已停止"
            : (STATUS_LABEL[row.status] ?? row.status);
        const details = [
          `role=${row.role}`,
          `status=${row.status}`,
          `source=${source}`,
          `pid=${row.pid}`,
          `alive=${row.alive}`,
          `heartbeat=${localTimestamp(agent.last_heartbeat)}`,
          `project=${row.project}`,
        ].join("\n");
        const dotClass = !agent.alive
          ? "bg-zinc-600"
          : pending
            ? "bg-amber-400 animate-pulse"
            : source === "external"
              ? "bg-sky-400"
              : "bg-emerald-400";

        return (
          <li key={`${role}-${row.pid}`}>
            <div
              role="button"
              tabIndex={onSelect && role ? 0 : undefined}
              aria-current={selected ? "true" : undefined}
              data-source={source}
              title={details}
              onClick={onSelect && role ? () => onSelect(role) : undefined}
              onKeyDown={
                onSelect && role
                  ? (event) => {
                      if (event.key === "Enter" || event.key === " ") {
                        event.preventDefault();
                        onSelect(role);
                      }
                    }
                  : undefined
              }
              className={`group flex cursor-pointer items-center gap-2.5 rounded-md px-2 py-1.5 transition-colors ${
                selected
                  ? "bg-zinc-800/70 ring-1 ring-inset ring-zinc-700/70"
                  : "hover:bg-zinc-900/80"
              }`}
            >
              <span
                aria-hidden
                className={`flex size-7 shrink-0 items-center justify-center rounded-md border text-[9px] font-semibold tracking-[0.06em] ${avatarTone(role)}`}
              >
                {initials(role) || "?"}
              </span>
              <span className="flex min-w-0 flex-1 flex-col">
                <span className="flex items-center gap-1.5">
                  <span className="truncate text-[13px] font-medium text-zinc-100">
                    {row.role || "—"}
                  </span>
                  <span
                    className={`size-1.5 shrink-0 rounded-full ${dotClass}`}
                    title={statusLabel}
                  />
                </span>
                <span className="truncate font-mono text-[10px] text-zinc-600">
                  {source === "external" ? "外部注册" : source === "managed" ? "中控托管" : "本地角色"}
                  {row.project !== "—" ? ` · ${shortProject(row.project)}` : ""}
                </span>
              </span>
              <span className="flex shrink-0 items-center gap-0.5 opacity-0 transition-opacity group-focus-within:opacity-100 group-hover:opacity-100">
                {source === "local" && onEdit ? (
                  <button
                    type="button"
                    aria-label={`编辑 ${role}`}
                    className="rounded p-1 text-zinc-500 hover:bg-zinc-800 hover:text-zinc-200"
                    onClick={(event) => {
                      event.stopPropagation();
                      onEdit(role);
                    }}
                  >
                    <Pencil className="size-3.5" />
                  </button>
                ) : null}
                {source === "local" && onDelete ? (
                  <button
                    type="button"
                    aria-label={`删除 ${role}`}
                    className="rounded p-1 text-zinc-500 hover:bg-red-500/10 hover:text-red-300"
                    onClick={(event) => {
                      event.stopPropagation();
                      onDelete(role);
                    }}
                  >
                    <Trash2 className="size-3.5" />
                  </button>
                ) : null}
                {source === "managed" ? (
                  <button
                    type="button"
                    aria-label={`停止 ${role}`}
                    disabled={!role || pending || agent.alive === false}
                    className="rounded p-1 text-zinc-500 hover:bg-zinc-800 hover:text-zinc-200 disabled:opacity-40"
                    onClick={(event) => {
                      event.stopPropagation();
                      onStop(role);
                    }}
                  >
                    <Square className="size-3.5" />
                  </button>
                ) : null}
              </span>
            </div>
          </li>
        );
      })}
    </ul>
  );
}
