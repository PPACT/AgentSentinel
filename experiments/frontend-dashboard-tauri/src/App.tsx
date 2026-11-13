import { ChevronUp, ListChecks, MessageSquare, Terminal } from "lucide-react";
import {
  lazy,
  Suspense,
  useCallback,
  useEffect,
  useRef,
  useState,
  type PointerEvent as ReactPointerEvent,
} from "react";

import { AgentsPanel } from "@/components/AgentsPanel";
import { CommandPalette } from "@/components/CommandPalette";
import { ConsolePanel } from "@/components/ConsolePanel";
import { ProgressPanel } from "@/components/ProgressPanel";
import { TaskDetailsDialog } from "@/components/TaskDetailsDialog";
import { TasksPanel } from "@/components/TasksPanel";
import { TopBar } from "@/components/TopBar";
import { WorkspaceDialog } from "@/components/WorkspaceDialog";
import {
  cleanupWildProcess,
  discoverAgents,
  ensureDaemon,
  ensureWorkspaceDaemon,
  eventStreamUrl,
  getStatus,
  getTasks,
  getWorkspaceAgent,
  getWorkspaces,
  getHandoff,
  logStreamUrl,
  setDaemonHome,
  shutdownAllDaemons,
  startHandoff,
  stopPty,
  stopRole,
  streamFetch,
  updateWorkspaceBudget,
} from "@/lib/backend";
import { errorMessage, formatTokenCount } from "@/lib/format";
import { appendBounded, logEventText, makeLogEntry, type LogEntry, type LogLevel } from "@/lib/log";
import { consumeEventStream, parseEventData } from "@/lib/sse";
import type {
  Agent,
  ConnectionState,
  DaemonInfo,
  EngineOption,
  HandoffStatus,
  StatusSnapshot,
  Task,
  WildProcess,
  Workspace,
  WorkspacesResponse,
} from "@/lib/types";import { useMediaQuery } from "@/lib/useMediaQuery";
import { usePtyChat } from "@/lib/usePtyChat";

// Markdown/高亮体积较大，聊天面板独立分包；主界面先渲染，面板异步加载。
const ChatPanel = lazy(() =>
  import("@/components/ChatPanel").then((module) => ({ default: module.ChatPanel })),
);
const TerminalPanel = lazy(() =>
  import("@/components/TerminalPanel").then((module) => ({ default: module.TerminalPanel })),
);

function isAgentOnline(agent: Agent | undefined): boolean {
  if (!agent || !agent.alive || !agent.agent_id) return false;
  return !["stopped", "lost", "failed", "pending"].includes(agent.status ?? "");
}

export default function App() {
  const [daemon, setDaemon] = useState<DaemonInfo | undefined>();
  const [agents, setAgents] = useState<Agent[]>([]);
  const [tasks, setTasks] = useState<Task[]>([]);
  const [selectedTask, setSelectedTask] = useState<Task | null>(null);
  const [connection, setConnection] = useState<ConnectionState>("connecting");
  const [error, setError] = useState<string | null>(null);
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [centerTab, setCenterTab] = useState<"chat" | "progress" | "terminal">("chat");
  const [selectedRole, setSelectedRole] = useState<string | null>(null);
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [engines, setEngines] = useState<EngineOption[]>([]);
  const [activeWorkspace, setActiveWorkspace] = useState<Workspace | null>(null);
  const [workspaceDialogOpen, setWorkspaceDialogOpen] = useState(false);
  const [workspaceEpoch, setWorkspaceEpoch] = useState(0);
  const [budgetOverride, setBudgetOverride] = useState<number | null>(null);
  const [handoff, setHandoff] = useState<HandoffStatus | null>(null);
  const fallbackRole = agents.find((agent) => isAgentOnline(agent) && agent.role)?.role ?? null;
  const selectedExists = agents.some((agent) => agent.role === selectedRole);
  const activeRole =
    selectedRole === null || (agents.length > 0 && !selectedExists) ? fallbackRole : selectedRole;
  const selectedAgent = agents.find((agent) => agent.role === activeRole);
  const selectedAgentId = selectedAgent?.agent_id ?? null;
  const selectedOnline = isAgentOnline(selectedAgent);
  const chatSupported =
    activeWorkspace === null ? false : (activeWorkspace.capabilities?.chat ?? true);
  const effectiveCenterTab = chatSupported ? centerTab : "terminal";
  const terminalLockReason = activeRole
    ? "当前 Agent 已离线，终端已锁定"
    : "请先在左侧选择在线 Agent";
  const chatLockReason = activeRole
    ? "当前 Agent 已离线，对话已锁定"
    : "请先在左侧选择在线 Agent";
  const ptyChat = usePtyChat(
    activeRole,
    selectedAgentId,
    selectedOnline && chatSupported,
    workspaceEpoch,
  );
  const activeHandoff =
    handoff && handoff.role === activeRole && handoff.agent_id === selectedAgentId
      ? handoff
      : null;
  const effectiveBudget =
    budgetOverride ?? activeWorkspace?.budget_tokens ?? ptyChat.budgetTokens;
  const [pendingRole, setPendingRole] = useState<string | null>(null);
  const [discovering, setDiscovering] = useState(false);
  const [cleaningPid, setCleaningPid] = useState<number | null>(null);
  const [wild, setWild] = useState<WildProcess[]>([]);
  const [wildOpen, setWildOpen] = useState(true);
  const [logs, setLogs] = useState<LogEntry[]>([]);
  const [tasksWidth, setTasksWidth] = useState(340);
  const [consoleOpen, setConsoleOpen] = useState(false);
  const [consoleHeight, setConsoleHeight] = useState(240);
  const [agentsDrawer, setAgentsDrawer] = useState(false);
  const [tasksDrawer, setTasksDrawer] = useState(false);
  const narrowTasks = useMediaQuery("(max-width: 1199px)");
  const narrowAgents = useMediaQuery("(max-width: 899px)");
  const taskDrag = useRef<{ startX: number; startWidth: number } | null>(null);
  const consoleDrag = useRef<{ startY: number; startHeight: number } | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const logAbortRef = useRef<AbortController | null>(null);
  const tasksTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const logIdRef = useRef(0);

  const appendLog = useCallback((level: LogLevel, text: string, at?: Date, role?: string) => {
    const entry = makeLogEntry(logIdRef.current++, level, text, at, role);
    setLogs((prev) => appendBounded(prev, entry));
  }, []);

  const applySnapshot = useCallback((snapshot: StatusSnapshot) => {
    setDaemon(snapshot.daemon);
    setAgents(Array.isArray(snapshot.agents) ? snapshot.agents : []);
  }, []);

  const refreshWorkspaces = useCallback(async (): Promise<WorkspacesResponse | null> => {
    try {
      const data = await getWorkspaces();
      setWorkspaces(data.workspaces ?? []);
      setEngines(data.engines ?? []);
      setActiveWorkspace(
        data.workspaces.find((workspace) => workspace.id === data.active_id) ??
          data.workspaces[0] ??
          null,
      );
      return data;
    } catch (err) {
      appendLog("warn", `读取工作区失败：${errorMessage(err)}`);
      return null;
    }
  }, [appendLog]);

  const handleHandoff = useCallback(async () => {
    if (!activeRole || !selectedAgentId || !activeWorkspace) return;
    try {
      const config = await getWorkspaceAgent(activeWorkspace.id, activeRole);
      if (!config.agent || !config.agent.configured) {
        const message = `当前 Agent（${activeRole}）未配置唤醒词`;
        setHandoff({
          active: false,
          phase: "error",
          message,
          error: `当前 Agent（${activeRole}）未配置唤醒词，请到 设置/工作区 → Agent 唤醒词 配置`,
          role: activeRole,
          agent_id: selectedAgentId,
        });
        appendLog("warn", message);
        return;
      }
      setHandoff(await startHandoff(activeRole, selectedAgentId));
    } catch (err) {
      const message = errorMessage(err);
      appendLog("error", `一键交接失败：${message}`);
      setError(message);
    }
  }, [activeRole, selectedAgentId, activeWorkspace, appendLog]);

  const handleBudgetChange = useCallback(
    async (tokens: number) => {
      if (!activeWorkspace) return;
      try {
        const data = await updateWorkspaceBudget(activeWorkspace.id, tokens);
        setBudgetOverride(tokens);
        setActiveWorkspace(data.workspace);
        setWorkspaces((previous) =>
          previous.map((workspace) =>
            workspace.id === data.workspace.id ? data.workspace : workspace,
          ),
        );
        appendLog("info", `上下文预算：${tokens.toLocaleString()} tokens`);
      } catch (err) {
        setError(errorMessage(err));
      }
    },
    [activeWorkspace, appendLog],
  );

  useEffect(() => {
    if (!handoff?.active) return;
    const timer = setInterval(() => {
      void getHandoff()
        .then((status) => {
          setHandoff(status);
          if (!status.active && status.phase === "done") {
            setWorkspaceEpoch((value) => value + 1);
          }
        })
        .catch(() => undefined);
    }, 500);
    return () => clearInterval(timer);
  }, [handoff?.active]);

  const refreshTasks = useCallback(async () => {
    try {
      const data = await getTasks();
      setTasks(Array.isArray(data.tasks) ? data.tasks : []);
    } catch (err) {
      setError(errorMessage(err));
    }
  }, []);

  /** 仅由 SSE snapshot 触发：去抖 250ms 重取 /tasks（事件驱动失效重取，不是轮询）。 */
  const scheduleTasksRefresh = useCallback(() => {
    if (tasksTimerRef.current !== null) clearTimeout(tasksTimerRef.current);
    tasksTimerRef.current = setTimeout(() => {
      tasksTimerRef.current = null;
      void refreshTasks();
    }, 250);
  }, [refreshTasks]);

  const startStream = useCallback(() => {
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    setConnection("connecting");
    appendLog("info", "正在连接中控事件流…");
    void consumeEventStream({
      url: eventStreamUrl,
      fetchImpl: streamFetch,
      signal: controller.signal,
      onFrame(frame) {
        const payload = parseEventData<StatusSnapshot>(frame);
        if (payload && (frame.event === "snapshot" || frame.event === "heartbeat")) {
          applySnapshot(payload);
          setError(null);
        }
        if (frame.event === "snapshot") {
          appendLog("info", `snapshot #${frame.id ?? "-"} · agents=${payload?.agents?.length ?? 0}`);
        }
        if (frame.event === "shutdown") {
          setConnection("closed");
          appendLog("warn", "中控已停止（shutdown）");
        }
        if (frame.event === "snapshot") scheduleTasksRefresh();
      },
      onState(state) {
        setConnection(state);
        if (state === "open") appendLog("info", "SSE 已连接");
        else if (state === "closed") appendLog("warn", "SSE 已关闭，准备重连");
        else if (state === "error") appendLog("warn", "SSE 连接中断，正在重连");
      },
      onError(err) {
        setError(errorMessage(err));
        appendLog("warn", errorMessage(err));
      },
    });
  }, [applySnapshot, appendLog, scheduleTasksRefresh]);

  /** 后端真实日志流（logs/<role>.log + daemon.log）；与状态 SSE 分开，互不影响。 */
  const startLogStream = useCallback(() => {
    logAbortRef.current?.abort();
    const controller = new AbortController();
    logAbortRef.current = controller;
    void consumeEventStream({
      url: async () => `${await logStreamUrl()}?tail=100`,
      fetchImpl: streamFetch,
      signal: controller.signal,
      onFrame(frame) {
        if (frame.event !== "log") return;
        const payload = parseEventData<{ role?: string; line?: string; ts?: string }>(frame);
        if (payload?.line) {
          const parsed = payload.ts ? new Date(payload.ts) : null;
          const at = parsed && !Number.isNaN(parsed.getTime()) ? parsed : undefined;
          appendLog("info", logEventText(payload.role, payload.line), at, payload.role);
        }
      },
      onState() {},
      onError() {
        // 状态流会提示连接错误；日志流保持静默重连，避免刷屏。
      },
    });
  }, [appendLog]);

  const reconnectStreams = useCallback(() => {
    startStream();
    startLogStream();
  }, [startLogStream, startStream]);

  const refreshAll = useCallback(async () => {
    try {
      applySnapshot(await getStatus());
      await refreshTasks();
      setError(null);
    } catch (err) {
      setError(errorMessage(err));
    }
  }, [applySnapshot, refreshTasks]);

  const switchWorkspace = useCallback(
    async (workspace: Workspace | null) => {
      abortRef.current?.abort();
      logAbortRef.current?.abort();
      setConnection("connecting");
      setDaemon(undefined);
      setAgents([]);
      setTasks([]);
      setSelectedRole(null);
      setHandoff(null);
      setBudgetOverride(null);
      try {
        if (workspace) await ensureWorkspaceDaemon(workspace);
        setDaemonHome(workspace?.daemon_home ?? null);
        setActiveWorkspace(workspace);
        setWorkspaceEpoch((value) => value + 1);
        await refreshAll();
        startStream();
        startLogStream();
        appendLog("info", `已切换工作区：${workspace?.name ?? "控制面"}`);
      } catch (err) {
        const message = errorMessage(err);
        setError(message);
        appendLog("error", message);
        setConnection("stopped");
      }
    },
    [appendLog, refreshAll, startLogStream, startStream],
  );

  const handleWorkspacesChanged = useCallback(
    (data: WorkspacesResponse) => {
      setWorkspaces(data.workspaces ?? []);
      setEngines(data.engines ?? []);
      const active =
        data.workspaces.find((workspace) => workspace.id === data.active_id) ??
        data.workspaces[0] ??
        null;
      if ((active?.daemon_home ?? null) !== (activeWorkspace?.daemon_home ?? null)) {
        void switchWorkspace(active);
        return;
      }
      setActiveWorkspace(active);
      setWorkspaceEpoch((value) => value + 1);
      void refreshAll();
      appendLog("info", `工作区已更新：${active?.name ?? "未选择"}`);
    },
    [activeWorkspace, appendLog, refreshAll, switchWorkspace],
  );

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        await ensureDaemon();
        if (cancelled) return;
        appendLog("info", "daemon 已就绪，加载状态…");
        const data = await refreshWorkspaces();
        const active =
          data?.workspaces.find((workspace) => workspace.id === data.active_id) ??
          data?.workspaces[0] ??
          null;
        if (active) await ensureWorkspaceDaemon(active);
        setDaemonHome(active?.daemon_home ?? null);
        setActiveWorkspace(active);
        await refreshAll();
        startStream();
        startLogStream();
      } catch (err) {
        if (!cancelled) {
          const message = errorMessage(err);
          setError(message);
          appendLog("error", message);
          setConnection("stopped");
        }
      }
    })();
    const onKeyDown = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setPaletteOpen(true);
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => {
      cancelled = true;
      window.removeEventListener("keydown", onKeyDown);
      abortRef.current?.abort();
      logAbortRef.current?.abort();
      if (tasksTimerRef.current !== null) clearTimeout(tasksTimerRef.current);
    };
  }, [appendLog, refreshAll, refreshWorkspaces, startLogStream, startStream]);

  // 点击窗口 X：先让 daemon 优雅退出（Job Object 回收 PTY/claude），确认后再销毁窗口。
  useEffect(() => {
    let unlisten: (() => void) | undefined;
    let disposed = false;
    let closing = false;
    void (async () => {
      try {
        const { getCurrentWindow } = await import("@tauri-apps/api/window");
        const currentWindow = getCurrentWindow();
        unlisten = await currentWindow.onCloseRequested(async (event) => {
          event.preventDefault();
          if (closing) return;
          closing = true;
          try {
            await shutdownAllDaemons();
          } catch (error) {
            appendLog("warn", `关闭中控时清理 daemon 失败：${errorMessage(error)}`);
          } finally {
            await currentWindow.destroy();
          }
        });
        if (disposed) {
          unlisten();
          unlisten = undefined;
        }
      } catch {
        // 非 Tauri 环境（单元测试）忽略
      }
    })();
    return () => {
      disposed = true;
      unlisten?.();
    };
  }, [appendLog]);

  // 切换 Agent 时释放上一个角色的 PTY；必须带旧 agent_id，避免误杀同名新 Agent。
  const previousRoleRef = useRef<{ role: string; agentId: string } | null>(null);
  useEffect(() => {
    const previous = previousRoleRef.current;
    previousRoleRef.current =
      activeRole && selectedAgentId ? { role: activeRole, agentId: selectedAgentId } : null;
    if (previous && (previous.role !== activeRole || previous.agentId !== selectedAgentId)) {
      void stopPty(previous.role, previous.agentId).catch(() => undefined);
    }
  }, [activeRole, selectedAgentId]);

  const handleStop = useCallback(
    async (role: string) => {
      setPendingRole(role);
      setAgents((prev) =>
        prev.map((agent) => (agent.role === role ? { ...agent, status: "stopping" } : agent)),
      );
      appendLog("info", `正在停止 ${role}…`);
      try {
        const agentId = agents.find((agent) => agent.role === role)?.agent_id;
        await stopRole(role, agentId);
        appendLog("info", `已停止 ${role}`);
        setError(null);
        await refreshTasks();
      } catch (err) {
        const message = errorMessage(err);
        appendLog("error", `停止 ${role} 失败：${message}`);
        setError(message);
        try {
          applySnapshot(await getStatus());
        } catch {
          // 保留乐观状态，等待下一次 SSE 覆盖
        }
      } finally {
        setPendingRole(null);
      }
    },
    [agents, appendLog, applySnapshot, refreshTasks],
  );

  const handleDiscover = useCallback(async () => {
    setDiscovering(true);
    try {
      const result = await discoverAgents();
      const found = Array.isArray(result.agents) ? result.agents : [];
      setAgents(found);
      const wildProcesses = Array.isArray(result.wild) ? result.wild : [];
      setWild(wildProcesses);
      const online = found.filter((agent) => agent.alive).length;
      const recovered = result.recovered ?? 0;
      const pruned = result.pruned ?? 0;
      appendLog(
        "info",
        `检测 Agent：${found.length} 个，在线 ${online} 个；缓存恢复 ${recovered}，清理 ${pruned}，未注册进程 ${wildProcesses.length}`,
      );
      setError(null);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setDiscovering(false);
    }
  }, [appendLog]);

  const handleCleanupWild = useCallback(
    async (process: WildProcess) => {
      if (process.pid === undefined || process.create_time === undefined) return;
      setCleaningPid(process.pid);
      try {
        const result = await cleanupWildProcess(process.pid, process.create_time);
        const killed = Array.isArray(result.killed) ? result.killed : [];
        appendLog("info", `强制清理 pid=${process.pid}：已结束 ${killed.join(", ") || "0"} 个进程`);
        setError(null);
        await handleDiscover();
      } catch (err) {
        setError(errorMessage(err));
      } finally {
        setCleaningPid(null);
      }
    },
    [appendLog, handleDiscover],
  );

  const onTasksResizeStart = (event: ReactPointerEvent<HTMLDivElement>) => {
    taskDrag.current = { startX: event.clientX, startWidth: tasksWidth };
    event.currentTarget.setPointerCapture(event.pointerId);
  };
  const onTasksResizeMove = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (!taskDrag.current) return;
    const delta = taskDrag.current.startX - event.clientX;
    setTasksWidth(Math.min(400, Math.max(300, taskDrag.current.startWidth + delta)));
  };
  const onTasksResizeEnd = (event: ReactPointerEvent<HTMLDivElement>) => {
    taskDrag.current = null;
    event.currentTarget.releasePointerCapture(event.pointerId);
  };

  const onConsoleResizeStart = (event: ReactPointerEvent<HTMLDivElement>) => {
    consoleDrag.current = { startY: event.clientY, startHeight: consoleHeight };
    event.currentTarget.setPointerCapture(event.pointerId);
  };
  const onConsoleResizeMove = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (!consoleDrag.current) return;
    const delta = consoleDrag.current.startY - event.clientY;
    const max = Math.max(220, window.innerHeight * 0.6);
    setConsoleHeight(Math.min(max, Math.max(140, consoleDrag.current.startHeight + delta)));
  };
  const onConsoleResizeEnd = (event: ReactPointerEvent<HTMLDivElement>) => {
    consoleDrag.current = null;
    event.currentTarget.releasePointerCapture(event.pointerId);
  };

  const agentsPane = (
    <AgentsPanel
      agents={agents}
      pendingRole={pendingRole}
      onStop={(role) => {
        void handleStop(role);
      }}
      selectedRole={activeRole}
      onSelectRole={setSelectedRole}
      wild={wild}
      wildOpen={wildOpen}
      onToggleWild={setWildOpen}
      discovering={discovering}
      onDiscover={() => {
        void handleDiscover();
      }}
      onCleanupWild={handleCleanupWild}
      cleaningPid={cleaningPid}
    />
  );
  const tasksPane = <TasksPanel tasks={tasks} onSelect={setSelectedTask} />;

  const contextWarning =
    ptyChat.contextTokens > 0
      ? `PTY 上下文约 ${formatTokenCount(ptyChat.contextTokens)} tokens；继续对话按输入计费（缓存命中亦有成本）`
      : null;

  return (
    <div className="flex h-screen flex-col bg-zinc-950 text-zinc-100">
      <TopBar
        daemon={daemon}
        connection={connection}
        onOpenCommand={() => setPaletteOpen(true)}
        showAgentsToggle={narrowAgents}
        onToggleAgents={() => setAgentsDrawer((value) => !value)}
        showTasksToggle={narrowTasks}
        onToggleTasks={() => setTasksDrawer((value) => !value)}
        workspaceName={activeWorkspace?.name}
        onOpenWorkspaces={() => setWorkspaceDialogOpen(true)}
      />
      {error ? (
        <div className="border-b border-red-500/30 bg-red-500/10 px-5 py-2 text-sm text-red-300">
          {error}
        </div>
      ) : null}

      <div className="flex min-h-0 flex-1">
        {narrowAgents ? null : (
          <aside className="w-60 shrink-0 border-r border-zinc-800 bg-zinc-950">
            {agentsPane}
          </aside>
        )}

        <main className="flex min-w-0 flex-1 flex-col">
          <div className="flex h-9 shrink-0 items-center gap-1 border-x border-b border-zinc-800 bg-zinc-950 px-2">
            {(
              [
                { key: "chat", label: "对话", icon: MessageSquare },
                { key: "progress", label: "进度记录", icon: ListChecks },
                { key: "terminal", label: "原生终端", icon: Terminal },
              ] as const
            )
              .filter((tab) => chatSupported || tab.key === "terminal")
              .map((tab) => {
              const Icon = tab.icon;
              return (
                <button
                  key={tab.key}
                  type="button"
                  role="tab"
                  aria-selected={effectiveCenterTab === tab.key}
                  onClick={() => setCenterTab(tab.key)}
                  className={`flex h-7 items-center gap-1.5 rounded px-3 text-[11px] transition-colors ${
                    effectiveCenterTab === tab.key
                      ? "bg-zinc-800 text-zinc-100"
                      : "text-zinc-500 hover:text-zinc-300"
                  }`}
                >
                  <Icon className="size-3.5" />
                  {tab.label}
                </button>
              );
            })}
            <span className="ml-auto pr-1 font-mono text-[10px] text-zinc-600">
              {effectiveCenterTab === "terminal"
                ? "PTY 常驻 · 原始 ANSI"
                : effectiveCenterTab === "progress"
                  ? "工具时间轴 · 零 token"
                  : "JSONL 旁路 · 零 token"}
            </span>
          </div>
          <div className="min-h-0 flex-1">
            {effectiveCenterTab === "chat" ? (
              <Suspense
                fallback={
                  <div className="flex h-full items-center justify-center text-xs text-zinc-600">
                    加载对话面板…
                  </div>
                }
              >
                <ChatPanel
                  entries={ptyChat.entries}
                  busy={ptyChat.busy}
                  sessionId={ptyChat.sessionId}
                  contextWarning={contextWarning}
                  contextTokens={ptyChat.contextTokens}
                  usage={ptyChat.usage}
                  budgetTokens={effectiveBudget}
                  notice={ptyChat.notice}
                  handoff={activeHandoff}
                  locked={!selectedOnline}
                  lockReason={chatLockReason}
                  onSend={(prompt) => {
                    void ptyChat.send(prompt);
                  }}
                  onStop={() => {
                    void ptyChat.stop();
                  }}
                  onHandoff={() => {
                    void handleHandoff();
                  }}
                  onBudgetChange={(tokens) => {
                    void handleBudgetChange(tokens);
                  }}
                />
              </Suspense>
            ) : effectiveCenterTab === "progress" ? (
              <ProgressPanel
                role={selectedOnline && ptyChat.sessionId ? activeRole : null}
                entries={ptyChat.entries}
              />
            ) : (
              <Suspense
                fallback={
                  <div className="flex h-full items-center justify-center text-xs text-zinc-600">
                    加载终端…
                  </div>
                }
              >
                <TerminalPanel
                  key={`${activeWorkspace?.id ?? "none"}:${activeRole ?? "none"}:${workspaceEpoch}`}
                  role={activeRole}
                  agentId={selectedAgentId}
                  workspaceName={activeWorkspace?.name ?? null}
                  locked={!selectedOnline}
                  lockReason={terminalLockReason}
                />
              </Suspense>
            )}
          </div>
        </main>

        {narrowTasks ? null : (
          <>
            <div
              role="separator"
              aria-orientation="vertical"
              className="w-1 shrink-0 cursor-col-resize bg-zinc-800/60 transition-colors hover:bg-sky-700/60"
              onPointerDown={onTasksResizeStart}
              onPointerMove={onTasksResizeMove}
              onPointerUp={onTasksResizeEnd}
              onPointerCancel={onTasksResizeEnd}
            />
            <aside
              style={{ width: tasksWidth }}
              className="shrink-0 border-l border-zinc-800 bg-zinc-950"
            >
              {tasksPane}
            </aside>
          </>
        )}
      </div>

      <div className="shrink-0 border-t border-zinc-800 bg-zinc-900/60">
        {consoleOpen ? (
          <div style={{ height: consoleHeight }} className="relative flex min-h-0 flex-col">
            <div
              role="separator"
              aria-orientation="horizontal"
              className="absolute -top-1 z-10 h-2 w-full cursor-row-resize"
              onPointerDown={onConsoleResizeStart}
              onPointerMove={onConsoleResizeMove}
              onPointerUp={onConsoleResizeEnd}
              onPointerCancel={onConsoleResizeEnd}
            />
            <ConsolePanel
              logs={logs}
              onClear={() => setLogs([])}
              onCollapse={() => setConsoleOpen(false)}
            />
          </div>
        ) : (
          <button
            type="button"
            className="flex h-8 w-full items-center gap-2 px-3 text-[11px] text-zinc-500 transition-colors hover:bg-zinc-800/40"
            onClick={() => setConsoleOpen(true)}
          >
            <Terminal className="size-3.5" />
            Console
            <span className="font-mono text-zinc-600">{logs.length}</span>
            <ChevronUp className="ml-auto size-3.5" />
          </button>
        )}
      </div>

      {narrowAgents && agentsDrawer ? (
        <div className="fixed inset-0 z-40">
          <button
            type="button"
            aria-label="关闭 Agents"
            className="absolute inset-0 bg-black/50"
            onClick={() => setAgentsDrawer(false)}
          />
          <aside className="absolute inset-y-0 left-0 z-10 w-64 border-r border-zinc-800 bg-zinc-950 shadow-2xl">
            {agentsPane}
          </aside>
        </div>
      ) : null}

      {narrowTasks && tasksDrawer ? (
        <div className="fixed inset-0 z-40">
          <button
            type="button"
            aria-label="关闭 Tasks"
            className="absolute inset-0 bg-black/50"
            onClick={() => setTasksDrawer(false)}
          />
          <aside className="absolute inset-y-0 right-0 z-10 w-[340px] border-l border-zinc-800 bg-zinc-950 shadow-2xl">
            {tasksPane}
          </aside>
        </div>
      ) : null}

      <CommandPalette
        open={paletteOpen}
        onOpenChange={setPaletteOpen}
        onRefresh={() => {
          void refreshAll();
        }}
        onReconnect={reconnectStreams}
        onClearLogs={() => setLogs([])}
      />
      <TaskDetailsDialog
        task={selectedTask}
        onOpenChange={(open) => {
          if (!open) setSelectedTask(null);
        }}
      />
      {workspaceDialogOpen ? (
        <WorkspaceDialog
          open={workspaceDialogOpen}
          onOpenChange={setWorkspaceDialogOpen}
          workspaces={workspaces}
          engines={engines}
          activeId={activeWorkspace?.id ?? null}
          onRefresh={handleWorkspacesChanged}
        />
      ) : null}
    </div>
  );
}
