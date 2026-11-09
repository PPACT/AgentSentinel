import { useCallback, useEffect, useRef, useState } from "react";

import { AgentTable } from "@/components/AgentTable";
import { CommandPalette } from "@/components/CommandPalette";
import { ConsolePanel } from "@/components/ConsolePanel";
import { TaskTable } from "@/components/TaskTable";
import { TopBar } from "@/components/TopBar";
import { ResizableHandle, ResizablePanel, ResizablePanelGroup } from "@/components/ui/resizable";
import {
  DAEMON_BASE,
  ensureDaemon,
  getStatus,
  getTasks,
  stopDaemon,
  stopRole,
  streamFetch,
} from "@/lib/backend";
import { errorMessage } from "@/lib/format";
import { consumeEventStream, parseEventData } from "@/lib/sse";
import type { Agent, ConnectionState, DaemonInfo, StatusSnapshot, Task } from "@/lib/types";

export default function App() {
  const [daemon, setDaemon] = useState<DaemonInfo | undefined>();
  const [agents, setAgents] = useState<Agent[]>([]);
  const [tasks, setTasks] = useState<Task[]>([]);
  const [connection, setConnection] = useState<ConnectionState>("connecting");
  const [error, setError] = useState<string | null>(null);
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [pendingRole, setPendingRole] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const tasksTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

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
    void consumeEventStream({
      url: `${DAEMON_BASE}/events`,
      fetchImpl: streamFetch,
      signal: controller.signal,
      onFrame(frame) {
        const payload = parseEventData<StatusSnapshot>(frame);
        if (payload && (frame.event === "snapshot" || frame.event === "heartbeat")) {
          applySnapshot(payload);
          setError(null);
        }
        if (frame.event === "shutdown") setConnection("closed");
        if (frame.event === "snapshot") scheduleTasksRefresh();
      },
      onState(state) {
        setConnection(state);
      },
      onError(err) {
        setError(errorMessage(err));
      },
    });
  }, [applySnapshot, scheduleTasksRefresh]);

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
        await refreshAll();
        startStream();
      } catch (err) {
        if (!cancelled) {
          setError(errorMessage(err));
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
      if (tasksTimerRef.current !== null) clearTimeout(tasksTimerRef.current);
      void stopDaemon();
    };
  }, [refreshAll, startStream]);

  const handleStop = useCallback(
    async (role: string) => {
      setPendingRole(role);
      try {
        await stopRole(role);
        setError(null);
        await refreshTasks();
      } catch (err) {
        setError(errorMessage(err));
      } finally {
        setPendingRole(null);
      }
    },
    [refreshTasks],
  );

  return (
    <div className="flex h-screen flex-col bg-zinc-950 text-zinc-100">
      <TopBar daemon={daemon} connection={connection} onOpenCommand={() => setPaletteOpen(true)} />
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
                <h2 className="mb-2 text-xs font-semibold uppercase tracking-wider text-sky-400">
                  Agents
                </h2>
                <div className="min-h-0 flex-1 overflow-auto rounded-lg border border-zinc-800">
                  <AgentTable
                    agents={agents}
                    pendingRole={pendingRole}
                    onStop={(role) => {
                      void handleStop(role);
                    }}
                  />
                </div>
              </section>
            </ResizablePanel>
            <ResizableHandle withHandle />
            <ResizablePanel defaultSize="40" minSize="25">
              <section className="flex h-full min-h-0 flex-col p-3">
                <h2 className="mb-2 text-xs font-semibold uppercase tracking-wider text-sky-400">
                  Tasks
                </h2>
                <div className="min-h-0 flex-1 overflow-auto rounded-lg border border-zinc-800">
                  <TaskTable tasks={tasks} />
                </div>
              </section>
            </ResizablePanel>
          </ResizablePanelGroup>
        </ResizablePanel>
        <ResizableHandle withHandle />
        <ResizablePanel defaultSize="28" minSize="10" collapsible>
          <ConsolePanel />
        </ResizablePanel>
      </ResizablePanelGroup>
      <CommandPalette
        open={paletteOpen}
        onOpenChange={setPaletteOpen}
        onRefresh={() => {
          void refreshAll();
        }}
        onReconnect={startStream}
      />
    </div>
  );
}
