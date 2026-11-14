import { FitAddon } from "@xterm/addon-fit";
import { WebglAddon } from "@xterm/addon-webgl";
import { Terminal } from "@xterm/xterm";
import "@xterm/xterm/css/xterm.css";
import { Lock } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { ptyStreamUrl, resizePty, sendPtyInput, startPty, streamFetch } from "@/lib/backend";
import { errorMessage } from "@/lib/format";
import { consumePtyStream } from "@/lib/pty";
import type { PtyNotice } from "@/lib/types";

type PtyState = "connecting" | "running" | "exited" | "error";

/** 输出合批窗口：24ms 合并多个流帧，减少 xterm 渲染次数（实测降低 CPU）。 */
const WRITE_BATCH_MS = 24;

const STATE_LABEL: Record<PtyState, string> = {
  connecting: "连接中…",
  running: "运行中",
  exited: "已退出",
  error: "连接失败",
};

interface TerminalPanelProps {
  role?: string | null;
  agentId?: string | null;
  workspaceName?: string | null;
  locked?: boolean;
  lockReason?: string;
  /** Tab 是否可见；隐藏时保持挂载与流连接，仅停渲染，避免重连回放 backlog。 */
  active?: boolean;
}

/** 原生 PTY 终端：xterm.js 渲染 ANSI；只允许写入当前选中且在线 Agent 的 PTY。 */
export function TerminalPanel({
  role = null,
  agentId = null,
  workspaceName = null,
  locked = false,
  lockReason = "当前 Agent 已离线，终端已锁定",
  active = true,
}: TerminalPanelProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const termRef = useRef<Terminal | null>(null);
  const fitRef = useRef<FitAddon | null>(null);
  const [state, setState] = useState<PtyState>("connecting");
  const [info, setInfo] = useState<{ command: string; sessionId: string } | null>(null);
  const [notice, setNotice] = useState<PtyNotice | null>(null);

  useEffect(() => {
    if (!notice) return;
    const timer = setTimeout(() => setNotice(null), 10_000);
    return () => clearTimeout(timer);
  }, [notice]);

  useEffect(() => {
    if (!role || !agentId || locked) {
      return;
    }
    const container = containerRef.current;
    if (!container) return;
    const term = new Terminal({
      fontFamily: '"JetBrains Mono", ui-monospace, SFMono-Regular, monospace',
      fontSize: 12.5,
      lineHeight: 1.2,
      cursorBlink: true,
      scrollback: 5000,
      convertEol: false,
      theme: {
        background: "#09090b",
        foreground: "#e4e4e7",
        cursor: "#38bdf8",
        selectionBackground: "#1e3a5f",
        black: "#18181b",
        brightBlack: "#52525b",
      },
    });
    const fit = new FitAddon();
    term.loadAddon(fit);
    term.open(container);
    termRef.current = term;
    fitRef.current = fit;
    if (import.meta.env.DEV) {
      (window as unknown as { __sentinelTerm?: Terminal }).__sentinelTerm = term;
    }
    try {
      fit.fit();
    } catch {
      // 容器尚未布局完成，ResizeObserver 会再次 fit
    }

    // WebGL 加速：加载失败或上下文丢失时回退默认渲染器，不允许黑屏。
    let webgl: WebglAddon | null = null;
    try {
      webgl = new WebglAddon();
      webgl.onContextLoss(() => {
        try {
          webgl?.dispose();
        } catch {
          // 已释放
        }
        webgl = null;
        try {
          term.refresh(0, Math.max(0, term.rows - 1));
        } catch {
          // 终端已销毁
        }
      });
      term.loadAddon(webgl);
    } catch {
      webgl = null;
    }

    const controller = new AbortController();
    const dataDisposable = term.onData((data) => {
      void sendPtyInput(data, role, agentId).catch(() => undefined);
    });

    let pendingChunks: Uint8Array[] = [];
    let flushTimer: ReturnType<typeof setTimeout> | null = null;
    const flush = () => {
      flushTimer = null;
      if (pendingChunks.length === 0) return;
      const chunks = pendingChunks;
      pendingChunks = [];
      const total = chunks.reduce((sum, chunk) => sum + chunk.length, 0);
      const merged = new Uint8Array(total);
      let offset = 0;
      for (const chunk of chunks) {
        merged.set(chunk, offset);
        offset += chunk.length;
      }
      term.write(merged);
    };
    const enqueue = (chunk: Uint8Array) => {
      pendingChunks.push(chunk);
      if (flushTimer === null) flushTimer = setTimeout(flush, WRITE_BATCH_MS);
    };
    const flushNow = () => {
      if (flushTimer !== null) {
        clearTimeout(flushTimer);
        flushTimer = null;
      }
      flush();
    };

    let resizeTimer: ReturnType<typeof setTimeout> | null = null;
    const applySize = () => {
      if (container.clientWidth <= 0) return;
      if (resizeTimer) clearTimeout(resizeTimer);
      resizeTimer = setTimeout(() => {
        try {
          fit.fit();
        } catch {
          return;
        }
        void resizePty(term.cols, term.rows, role, agentId).catch(() => undefined);
      }, 150);
    };
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(applySize);
    observer?.observe(container);

    const connect = async () => {
      try {
        setState("connecting");
        const status = await startPty(role, agentId);
        setInfo({ command: status.command, sessionId: status.session_id });
        const response = await streamFetch(await ptyStreamUrl(role, agentId), {
          headers: { Accept: "text/event-stream" },
          signal: controller.signal,
        });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        setState("running");
        await consumePtyStream(response, {
          onData: (chunk) => enqueue(chunk),
          onNotice: (next) => setNotice(next),
          onExit: (status) => {
            flushNow();
            setState("exited");
            term.write(`\r\n\x1b[33m[PTY exited${status === null ? "" : `: ${status}`}]\x1b[0m\r\n`);
          },
        });
        if (!controller.signal.aborted) {
          flushNow();
          setState("exited");
          term.write("\r\n\x1b[33m[PTY stream closed]\x1b[0m\r\n");
        }
      } catch (err) {
        if (controller.signal.aborted) return;
        setState("error");
        term.write(`\r\n\x1b[31m${errorMessage(err)}\x1b[0m\r\n`);
      }
    };
    void connect();

    return () => {
      controller.abort();
      if (resizeTimer) clearTimeout(resizeTimer);
      if (flushTimer !== null) clearTimeout(flushTimer);
      pendingChunks = [];
      observer?.disconnect();
      dataDisposable.dispose();
      termRef.current = null;
      fitRef.current = null;
      if ((window as unknown as { __sentinelTerm?: Terminal }).__sentinelTerm === term) {
        delete (window as unknown as { __sentinelTerm?: Terminal }).__sentinelTerm;
      }
      term.dispose();
    };
  }, [role, agentId, locked]);

  // Tab 切回时重新 fit；终端全程保持挂载，不重连 /pty/stream。
  useEffect(() => {
    if (!active || !role || !agentId) return;
    const timer = setTimeout(() => {
      const container = containerRef.current;
      const term = termRef.current;
      if (!container || !term || container.clientWidth <= 0) return;
      try {
        fitRef.current?.fit();
      } catch {
        return;
      }
      void resizePty(term.cols, term.rows, role, agentId).catch(() => undefined);
    }, 16);
    return () => clearTimeout(timer);
  }, [active, role, agentId]);

  const displayState: PtyState = !role || !agentId || locked ? "exited" : state;
  const displayInfo = !role || !agentId || locked ? null : info;

  return (
    <section className="relative flex h-full min-h-0 min-w-0 flex-col bg-[#09090b]">
      <div className="flex h-8 shrink-0 items-center gap-2 border-x border-b border-zinc-800 bg-zinc-950 px-3 text-[11px] text-zinc-500">
        <span
          className={`size-1.5 rounded-full ${
            locked
              ? "bg-zinc-500"
              : displayState === "running"
                ? "bg-emerald-400"
                : displayState === "error"
                  ? "bg-red-400"
                  : "bg-amber-400"
          }`}
        />
        <span className="text-zinc-300">Terminal</span>
        <span className="font-mono text-zinc-600">
          {workspaceName ? `${workspaceName} · ` : ""}
          {role ? `${role} · ` : ""}
          {displayInfo ? `${displayInfo.command} · PTY` : "claude · PTY"}
        </span>
        {displayInfo?.sessionId ? (
          <span
            className="truncate font-mono text-[10px] text-zinc-700"
            title={displayInfo.sessionId}
          >
            session {displayInfo.sessionId.slice(0, 8)}
          </span>
        ) : null}
        <span className="ml-auto font-mono text-zinc-600">
          {locked ? "已锁定" : STATE_LABEL[displayState]}
        </span>
      </div>
      {notice ? (
        <div className="shrink-0 border-b border-amber-500/30 bg-amber-500/10 px-3 py-1.5 text-[11px] text-amber-200/90">
          {notice.message}
        </div>
      ) : null}
      <div ref={containerRef} className="min-h-0 flex-1 overflow-hidden px-2 py-1" />
      {locked ? (
        <div className="absolute inset-0 z-10 flex flex-col items-center justify-center gap-2 bg-zinc-950/85 backdrop-blur-sm">
          <Lock className="size-5 text-amber-400/80" />
          <span className="text-xs text-zinc-300">{lockReason}</span>
          <span className="font-mono text-[10px] text-zinc-600">
            {role ? `role=${role}` : "未选择 Agent"} · 输入已禁用
          </span>
        </div>
      ) : null}
    </section>
  );
}
