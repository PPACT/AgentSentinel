import { useCallback, useEffect, useRef, useState } from "react";

import {
  getPtyHistory,
  ptyChatStreamUrl,
  sendPtyInput,
  startPty,
  streamFetch,
} from "./backend";
import type { ChatEntry } from "./chat";
import { errorMessage } from "./format";
import { consumePtyChatStream, type PtyChatMessage } from "./pty";

export interface PtyChatState {
  entries: ChatEntry[];
  busy: boolean;
  contextTokens: number;
  sessionId: string | null;
  error: string | null;
  send: (prompt: string) => Promise<void>;
  stop: () => Promise<void>;
}

const BUSY_TIMEOUT_MS = 180_000;

/**
 * 对话页唯一数据源：JSONL 旁路。输入直接写入同一 PTY 的 stdin；
 * 终端/对话共享一个 claude 会话，消息从本地 transcript 增量读取。
 */
export function usePtyChat(): PtyChatState {
  const [entries, setEntries] = useState<ChatEntry[]>([]);
  const [busy, setBusy] = useState(false);
  const [contextTokens, setContextTokens] = useState(0);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const nextId = useRef(0);
  const busyTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const toEntries = useCallback((messages: PtyChatMessage[]): ChatEntry[] => {
    const items: ChatEntry[] = [];
    for (const message of messages) {
      for (const block of message.blocks) {
        // transcript 里 user 的文本块也是 kind=text，这里恢复成用户气泡。
        const normalized =
          message.role === "user" && block.kind === "text"
            ? ({ kind: "user", text: block.text } as const)
            : block;
        items.push({ id: nextId.current++, block: normalized, timestamp: message.timestamp ?? null });
      }
    }
    return items;
  }, []);

  const appendMessages = useCallback(
    (messages: PtyChatMessage[]) => {
      const items = toEntries(messages);
      if (items.length > 0) setEntries((previous) => [...previous, ...items]);
      const hasText = messages.some(
        (message) =>
          message.role === "assistant" && message.blocks.some((block) => block.kind === "text"),
      );
      if (hasText) {
        setBusy(false);
        if (busyTimer.current) clearTimeout(busyTimer.current);
      }
    },
    [toEntries],
  );

  useEffect(() => {
    const controller = new AbortController();
    let cancelled = false;
    const connect = async () => {
      try {
        const history = await getPtyHistory();
        if (cancelled) return;
        setSessionId(history.session_id);
        setContextTokens(history.context_tokens);
        setEntries(toEntries(history.messages));
        const response = await streamFetch(await ptyChatStreamUrl(history.offset), {
          headers: { Accept: "text/event-stream" },
          signal: controller.signal,
        });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        await consumePtyChatStream(response, {
          onMessages: appendMessages,
          onContext: (tokens) => setContextTokens((previous) => Math.max(previous, tokens)),
          onSession: (sid) => {
            if (!cancelled) setSessionId(sid);
          },
          onExit: () => {
            if (!cancelled) setBusy(false);
          },
        });
      } catch (err) {
        if (!controller.signal.aborted) setError(errorMessage(err));
      }
    };
    void connect();
    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [appendMessages, toEntries]);

  const send = useCallback(async (prompt: string) => {
    const text = prompt.trim();
    if (!text) return;
    setBusy(true);
    setError(null);
    if (busyTimer.current) clearTimeout(busyTimer.current);
    busyTimer.current = setTimeout(() => setBusy(false), BUSY_TIMEOUT_MS);
    try {
      const status = await startPty();
      setSessionId(status.session_id);
      await sendPtyInput(`${text}\r`);
    } catch (err) {
      setBusy(false);
      setError(errorMessage(err));
    }
  }, []);

  const stop = useCallback(async () => {
    setBusy(false);
    if (busyTimer.current) clearTimeout(busyTimer.current);
    try {
      await sendPtyInput("\u0003");
    } catch {
      // PTY 已退出时忽略
    }
  }, []);

  return { entries, busy, contextTokens, sessionId, error, send, stop };
}
