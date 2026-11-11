import { ChevronUp, Terminal } from "lucide-react";
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
import { TaskDetailsDialog } from "@/components/TaskDetailsDialog";
import { TasksPanel } from "@/components/TasksPanel";
import { TopBar } from "@/components/TopBar";
import {
  cleanupWildProcess,
  discoverAgents,
  ensureDaemon,
  eventStreamUrl,
  getStatus,
  getTasks,
  logStreamUrl,
  stopDaemon,
  stopRole,
  streamFetch,
} from "@/lib/backend";
import { errorMessage } from "@/lib/format";
import { appendBounded, logEventText, makeLogEntry, type LogEntry, type LogLevel } from "@/lib/log";
import { consumeEventStream, parseEventData } from "@/lib/sse";
import type { Agent, ConnectionState, DaemonInfo, StatusSnapshot, Task, WildProcess } from "@/lib/types";
import { useMediaQuery } from "@/lib/useMediaQuery";
import { useChatSessions } from "@/lib/useChatSessions";

// Markdown/高亮体积较大，聊天面板独立分包；主界面先渲染，面板异步加载。
const ChatPanel = lazy(() =>
  import("@/components/ChatPanel").then((module) => ({ default: module.ChatPanel })),
);

export default function App() {
  const [daemon, setDaemon] = useState<DaemonInfo | undefined>();
  const [agents, setAgents] = useState<Agent[]>([]);
  const [tasks, setTasks] = useState<Task[]>([]);
  const [selectedTask, setSelectedTask] = useState<Task | null>(null);
  const [connection, setConnection] = useState<ConnectionState>("connecting");
  const [error, setError] = useState<string | null>(null);
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [selectedRole, setSelectedRole] = useState<string | null>(null);
  const { histories: chatHistories, busy: chatBusy, inherited: chatInherited, send: sendChat, stop: stopChat } =
    useChatSessions();
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

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        await ensureDaemon();
        if (cancelled) return;
        appendLog("info", "daemon 已就绪，加载状态…");
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
      void stopDaemon();
    };
  }, [appendLog, refreshAll, startLogStream, startStream]);

  const handleStop = useCallback(
    async (role: string) => {
      setPendingRole(role);
      setAgents((prev) =>
        prev.map((agent) => (agent.role === role ? { ...agent, status: "stopping" } : agent)),
      );
      appendLog("info", `正在停止 ${role}…`);
      try {
        await stopRole(role);
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
    [appendLog, applySnapshot, refreshTasks],
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
      selectedRole={selectedRole}
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

  const selectedEntries = selectedRole ? (chatHistories[selectedRole] ?? []) : [];
  const selectedAgent = agents.find((agent) => agent.role === selectedRole);
  const inheritedFrom = selectedRole ? chatInherited[selectedRole] : undefined;
  const inheritHint = !selectedRole
    ? null
    : inheritedFrom
      ? `已继承 ${selectedRole} 终端上下文，当前为独立会话分支`
      : selectedEntries.length === 0 && selectedAgent?.external_session_id
        ? `首次发送将继承 ${selectedRole} 终端上下文（独立分支）`
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

        <main className="min-w-0 flex-1">
          <Suspense
            fallback={
              <div className="flex h-full items-center justify-center text-xs text-zinc-600">
                加载对话面板…
              </div>
            }
          >
            <ChatPanel
              role={selectedRole}
              entries={selectedEntries}
              busy={selectedRole ? (chatBusy[selectedRole] ?? false) : false}
              inheritHint={inheritHint}
              onSend={(prompt) => {
                if (selectedRole) void sendChat(selectedRole, prompt);
              }}
              onStop={() => {
                if (selectedRole) stopChat(selectedRole);
              }}
            />
          </Suspense>
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
    </div>
  );
}
