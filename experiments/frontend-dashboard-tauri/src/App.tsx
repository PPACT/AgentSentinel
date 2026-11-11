import { useCallback, useEffect, useRef, useState } from "react";

import { AgentTable } from "@/components/AgentTable";
import { ChatDialog } from "@/components/ChatDialog";
import { CommandPalette } from "@/components/CommandPalette";
import { ConsolePanel } from "@/components/ConsolePanel";
import { TaskDetailsDialog } from "@/components/TaskDetailsDialog";
import { TaskTable } from "@/components/TaskTable";
import { TopBar } from "@/components/TopBar";
import { WildTable } from "@/components/WildTable";
import { Button } from "@/components/ui/button";
import { ResizableHandle, ResizablePanel, ResizablePanelGroup } from "@/components/ui/resizable";
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

export default function App() {
  const [daemon, setDaemon] = useState<DaemonInfo | undefined>();
  const [agents, setAgents] = useState<Agent[]>([]);
  const [tasks, setTasks] = useState<Task[]>([]);
  const [selectedTask, setSelectedTask] = useState<Task | null>(null);
  const [connection, setConnection] = useState<ConnectionState>("connecting");
  const [error, setError] = useState<string | null>(null);
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [chatOpen, setChatOpen] = useState(false);
  const [pendingRole, setPendingRole] = useState<string | null>(null);
  const [discovering, setDiscovering] = useState(false);
  const [cleaningPid, setCleaningPid] = useState<number | null>(null);
  const [wild, setWild] = useState<WildProcess[]>([]);
  const [wildOpen, setWildOpen] = useState(true);
  const [logs, setLogs] = useState<LogEntry[]>([]);
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

  return (
    <div className="flex h-screen flex-col bg-zinc-950 text-zinc-100">
      <TopBar
        daemon={daemon}
        connection={connection}
        onOpenCommand={() => setPaletteOpen(true)}
        onOpenChat={() => setChatOpen(true)}
      />
      <ChatDialog open={chatOpen} onOpenChange={setChatOpen} />
      {error ? (
        <div className="border-b border-red-500/30 bg-red-500/10 px-5 py-2 text-sm text-red-300">
          {error}
        </div>
      ) : null}
      <ResizablePanelGroup orientation="vertical" className="min-h-0 flex-1">
        <ResizablePanel defaultSize="72" minSize="30">
          <ResizablePanelGroup orientation="horizontal" className="min-h-0">
            <ResizablePanel defaultSize="60" minSize="30">
              <section className="flex h-full min-h-0 flex-col p-3">
                <div className="mb-2 flex items-center justify-between gap-2">
                  <h2 className="text-xs font-semibold uppercase tracking-wider text-sky-400">
                    Agents
                  </h2>
                  <Button
                    variant="outline"
                    size="sm"
                    className="h-6 px-2 text-xs"
                    disabled={discovering}
                    onClick={() => {
                      void handleDiscover();
                    }}
                  >
                    {discovering ? "检测中…" : "检测 Agent"}
                  </Button>
                </div>
                <div className="min-h-0 flex-1 overflow-auto rounded-lg border border-zinc-800">
                  <AgentTable
                    agents={agents}
                    pendingRole={pendingRole}
                    onStop={(role) => {
                      void handleStop(role);
                    }}
                  />
                </div>
                {wild.length > 0 ? (
                  <details
                    className="mt-2 rounded-lg border border-zinc-800 bg-zinc-900/40"
                    open={wildOpen}
                    onToggle={(event) => setWildOpen(event.currentTarget.open)}
                  >
                    <summary className="cursor-pointer select-none px-3 py-1.5 text-xs font-medium text-amber-300/90">
                      未注册进程（{wild.length}）
                    </summary>
                    <div className="max-h-40 overflow-auto border-t border-zinc-800">
                      <WildTable
                        processes={wild}
                        onCleanup={handleCleanupWild}
                        busyPid={cleaningPid}
                      />
                    </div>
                  </details>
                ) : null}
              </section>
            </ResizablePanel>
            <ResizableHandle withHandle />
            <ResizablePanel defaultSize="40" minSize="25">
              <section className="flex h-full min-h-0 flex-col p-3">
                <h2 className="mb-2 text-xs font-semibold uppercase tracking-wider text-sky-400">
                  Tasks
                </h2>
                <div className="min-h-0 flex-1 overflow-auto rounded-lg border border-zinc-800">
                  <TaskTable tasks={tasks} onSelect={setSelectedTask} />
                </div>
              </section>
            </ResizablePanel>
          </ResizablePanelGroup>
        </ResizablePanel>
        <ResizableHandle withHandle />
        <ResizablePanel defaultSize="28" minSize="10" collapsible>
          <ConsolePanel logs={logs} onClear={() => setLogs([])} />
        </ResizablePanel>
      </ResizablePanelGroup>
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
