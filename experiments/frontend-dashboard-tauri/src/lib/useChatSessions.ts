import { useCallback, useRef, useState } from "react";

import { startChatStream } from "./backend";
import { consumeChatStream, type ChatBlock, type ChatEntry } from "./chat";
import { errorMessage } from "./format";

export interface ChatSessionsState {
  histories: Record<string, ChatEntry[]>;
  busy: Record<string, boolean>;
  inherited: Record<string, string>;
  send: (role: string, prompt: string) => Promise<void>;
  stop: (role: string) => void;
}

/**
 * 多角色独立对话。histories 以 role 为 key；同角色同步 in-flight 锁，
 * 不同角色可并行。这里没有任何 effect，发送只能由用户事件触发。
 */
export function useChatSessions(): ChatSessionsState {
  const [histories, setHistories] = useState<Record<string, ChatEntry[]>>({});
  const [busy, setBusy] = useState<Record<string, boolean>>({});
  const [inherited, setInherited] = useState<Record<string, string>>({});
  const inFlight = useRef(new Set<string>());
  const aborters = useRef(new Map<string, AbortController>());
  const nextId = useRef(0);

  const append = useCallback((role: string, blocks: ChatBlock[]) => {
    if (blocks.length === 0) return;
    setHistories((previous) => ({
      ...previous,
      [role]: [
        ...(previous[role] ?? []),
        ...blocks.map((block) => ({ id: nextId.current++, block })),
      ],
    }));
  }, []);

  const send = useCallback(
    async (role: string, prompt: string) => {
      const text = prompt.trim();
      if (!text || inFlight.current.has(role)) return;
      inFlight.current.add(role);
      append(role, [{ kind: "user", text }]);
      setBusy((previous) => ({ ...previous, [role]: true }));
      const controller = new AbortController();
      aborters.current.set(role, controller);
      try {
        const response = await startChatStream({ role, prompt: text, signal: controller.signal });
        await consumeChatStream(response, {
          onBlocks: (blocks) => append(role, blocks),
          onEnd: (summary) => {
            const from = summary.inherited_from;
            if (from) setInherited((previous) => ({ ...previous, [role]: from }));
          },
        });
      } catch (err) {
        if (!controller.signal.aborted) {
          append(role, [{ kind: "error", text: errorMessage(err) }]);
        }
      } finally {
        aborters.current.delete(role);
        inFlight.current.delete(role);
        setBusy((previous) => ({ ...previous, [role]: false }));
      }
    },
    [append],
  );

  const stop = useCallback(
    (role: string) => {
      aborters.current.get(role)?.abort();
      append(role, [{ kind: "meta", text: "已请求停止本轮" }]);
    },
    [append],
  );

  return { histories, busy, inherited, send, stop };
}
