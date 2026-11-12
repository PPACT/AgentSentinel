import { FitAddon } from "@xterm/addon-fit";
import { Terminal } from "@xterm/xterm";
import "@xterm/xterm/css/xterm.css";
import { useEffect, useRef, useState } from "react";

import { ptyStreamUrl, resizePty, sendPtyInput, startPty, streamFetch } from "@/lib/backend";
import { errorMessage } from "@/lib/format";
import { consumePtyStream } from "@/lib/pty";

type PtyState = "connecting" | "running" | "exited" | "error";

const STATE_LABEL: Record<PtyState, string> = {
  connecting: "连接中…",
  running: "运行中",
  exited: "已退出",
  error: "连接失败",
};

/** 原生 PTY 终端：xterm.js 渲染 ANSI，输入直接写入同一 PTY。 */
export function TerminalPanel() {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const [state, setState] = useState<PtyState>("connecting");
  const [info, setInfo] = useState<{ command: string; sessionId: string } | null>(null);

  useEffect(() => {
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
    if (import.meta.env.DEV) {
      (window as unknown as { __sentinelTerm?: Terminal }).__sentinelTerm = term;
    }
    try {
      fit.fit();
    } catch {
      // 容器尚未布局完成，ResizeObserver 会再次 fit
    }

    const controller = new AbortController();
    const dataDisposable = term.onData((data) => {
      void sendPtyInput(data).catch(() => undefined);
    });

    let resizeTimer: ReturnType<typeof setTimeout> | null = null;
    const applySize = () => {
      if (resizeTimer) clearTimeout(resizeTimer);
      resizeTimer = setTimeout(() => {
        try {
          fit.fit();
        } catch {
          return;
        }
        void resizePty(term.cols, term.rows).catch(() => undefined);
      }, 150);
    };
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(applySize);
    observer?.observe(container);

    const connect = async () => {
      try {
        setState("connecting");
        const status = await startPty();
        setInfo({ command: status.command, sessionId: status.session_id });
        const response = await streamFetch(await ptyStreamUrl(), {
          headers: { Accept: "text/event-stream" },
          signal: controller.signal,
        });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        setState("running");
        await consumePtyStream(response, {
          onData: (chunk) => term.write(chunk),
          onExit: (status) => {
            setState("exited");
            term.write(`\r\n\x1b[33m[PTY exited${status === null ? "" : `: ${status}`}]\x1b[0m\r\n`);
          },
        });
        if (!controller.signal.aborted) {
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
      observer?.disconnect();
      dataDisposable.dispose();
      if ((window as unknown as { __sentinelTerm?: Terminal }).__sentinelTerm === term) {
        delete (window as unknown as { __sentinelTerm?: Terminal }).__sentinelTerm;
      }
      term.dispose();
    };
  }, []);

  return (
    <section className="flex h-full min-h-0 min-w-0 flex-col bg-[#09090b]">
      <div className="flex h-8 shrink-0 items-center gap-2 border-x border-b border-zinc-800 bg-zinc-950 px-3 text-[11px] text-zinc-500">
        <span
          className={`size-1.5 rounded-full ${
            state === "running"
              ? "bg-emerald-400"
              : state === "error"
                ? "bg-red-400"
                : "bg-amber-400"
          }`}
        />
        <span className="text-zinc-300">Terminal</span>
        <span className="font-mono text-zinc-600">
          {info ? `${info.command} · PTY` : "claude · PTY"}
        </span>
        {info?.sessionId ? (
          <span className="truncate font-mono text-[10px] text-zinc-700" title={info.sessionId}>
            session {info.sessionId.slice(0, 8)}
          </span>
        ) : null}
        <span className="ml-auto font-mono text-zinc-600">{STATE_LABEL[state]}</span>
      </div>
      <div ref={containerRef} className="min-h-0 flex-1 overflow-hidden px-2 py-1" />
    </section>
  );
}
