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
import type { PtyNotice, TokenUsage } from "./types";

const EMPTY_USAGE: TokenUsage = {
  input_tokens: 0,
  output_tokens: 0,
  cache_read_input_tokens: 0,
  cache_creation_input_tokens: 0,
};

function mergeUsage(total: TokenUsage, delta: TokenUsage): TokenUsage {
  return {
    input_tokens: total.input_tokens + delta.input_tokens,
    output_tokens: total.output_tokens + delta.output_tokens,
    cache_read_input_tokens: total.cache_read_input_tokens + delta.cache_read_input_tokens,
    cache_creation_input_tokens:
      total.cache_creation_input_tokens + delta.cache_creation_input_tokens,
  };
}

export interface PtyChatState {
  entries: ChatEntry[];
  busy: boolean;
  contextTokens: number;
  usage: TokenUsage;
  budgetTokens: number;
  notice: PtyNotice | null;
  sessionId: string | null;
  error: string | null;
  send: (prompt: string) => Promise<void>;
  stop: () => Promise<void>;
}

const BUSY_TIMEOUT_MS = 180_000;

/**
 * 对话页唯一数据源：JSONL 旁路。输入直接写入同一 PTY 的 stdin；
 * 终端/对话共享一个绑定当前选中 Agent 的 claude 会话，消息从本地 transcript 增量读取。
 * role 为空或 Agent 不在线时不上线、不启动 PTY。
 */
export function usePtyChat(
  role: string | null,
  agentId: string | null,
  enabled: boolean,
  generation = 0,
): PtyChatState {
  const [entries, setEntries] = useState<ChatEntry[]>([]);
  const [busy, setBusy] = useState(false);
  const [contextTokens, setContextTokens] = useState(0);
  const [usage, setUsage] = useState<TokenUsage>(EMPTY_USAGE);
  const [budgetTokens, setBudgetTokens] = useState(0);
  const [notice, setNotice] = useState<PtyNotice | null>(null);
  const [epoch, setEpoch] = useState(0);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const nextId = useRef(0);
  const busyTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const lastBindRef = useRef<string | null>(null);

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
    if (!role || !agentId || !enabled) {
      return;
    }
    const controller = new AbortController();
    let cancelled = false;
    const bindKey = `${role}:${agentId}:${generation}`;
    const connect = async () => {
      if (lastBindRef.current !== bindKey) {
        lastBindRef.current = bindKey;
        setNotice(null);
      }
      setEntries([]);
      setSessionId(null);
      setContextTokens(0);
      setUsage(EMPTY_USAGE);
      try {
        const history = await getPtyHistory(role, agentId);
        if (cancelled) return;
        setSessionId(history.session_id);
        setContextTokens(history.context_tokens);
        setUsage(history.usage ?? EMPTY_USAGE);
        setBudgetTokens(history.budget_tokens ?? 0);
        setEntries(toEntries(history.messages));
        const response = await streamFetch(await ptyChatStreamUrl(history.offset, role, agentId), {
          headers: { Accept: "text/event-stream" },
          signal: controller.signal,
        });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        await consumePtyChatStream(response, {
          onMessages: appendMessages,
          onContext: (tokens) => setContextTokens((previous) => Math.max(previous, tokens)),
          onUsage: (delta) => setUsage((previous) => mergeUsage(previous, delta)),
          onNotice: (next) => setNotice(next),
          onSession: (sid) => {
            if (!cancelled) {
              setSessionId(sid);
              setEpoch((value) => value + 1);
            }
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
  }, [appendMessages, toEntries, role, agentId, enabled, epoch, generation]);

  const send = useCallback(
    async (prompt: string) => {
      const text = prompt.trim();
      if (!text || !role || !agentId || !enabled) return;
      setBusy(true);
      setError(null);
      if (busyTimer.current) clearTimeout(busyTimer.current);
      busyTimer.current = setTimeout(() => setBusy(false), BUSY_TIMEOUT_MS);
      try {
        const status = await startPty(role, agentId);
        setSessionId(status.session_id);
        await sendPtyInput(`${text}\r`, role, agentId);
      } catch (err) {
        setBusy(false);
        setError(errorMessage(err));
      }
    },
    [role, agentId, enabled],
  );

  const stop = useCallback(async () => {
    setBusy(false);
    if (busyTimer.current) clearTimeout(busyTimer.current);
    if (!role || !agentId) return;
    try {
      await sendPtyInput("\u0003", role, agentId);
    } catch {
      // PTY 已退出时忽略
    }
  }, [role, agentId]);

  const active = Boolean(role) && Boolean(agentId) && enabled;
  return {
    entries: active ? entries : [],
    busy: active ? busy : false,
    contextTokens: active ? contextTokens : 0,
    usage: active ? usage : EMPTY_USAGE,
    budgetTokens: active ? budgetTokens : 0,
    notice: active ? notice : null,
    sessionId: active ? sessionId : null,
    error: active ? error : null,
    send,
    stop,
  };
}
