import { useEffect, useRef, useState } from "react";

import { ApiError, getSessionHistory } from "./backend";
import type { ChatEntry } from "./chat";
import { errorMessage } from "./format";

export interface TerminalHistoryData {
  entries: ChatEntry[];
  contextTokens: number;
  bytes: number;
  truncated: boolean;
  sessionId: string;
}

interface TerminalHistoryState {
  data: Record<string, TerminalHistoryData>;
  loading: Record<string, boolean>;
  errors: Record<string, string>;
}

const EMPTY: TerminalHistoryData = {
  entries: [],
  contextTokens: 0,
  bytes: 0,
  truncated: false,
  sessionId: "",
};

/** 选中角色时读取一次终端 transcript；只读、零 token，不轮询。 */
export function useTerminalHistory(role: string | null): TerminalHistoryState {
  const [data, setData] = useState<Record<string, TerminalHistoryData>>({});
  const [loading, setLoading] = useState<Record<string, boolean>>({});
  const [errors, setErrors] = useState<Record<string, string>>({});
  const requested = useRef(new Set<string>());

  useEffect(() => {
    if (!role || requested.current.has(role)) return;
    requested.current.add(role);
    setLoading((previous) => ({ ...previous, [role]: true }));
    getSessionHistory(role)
      .then((response) => {
        let nextId = 0;
        const entries: ChatEntry[] = response.messages.flatMap((message) =>
          message.blocks.map((block) => ({
            id: nextId++,
            block,
            timestamp: message.timestamp ?? null,
          })),
        );
        setData((previous) => ({
          ...previous,
          [role]: {
            entries,
            contextTokens: response.context_tokens,
            bytes: response.bytes,
            truncated: response.truncated,
            sessionId: response.session_id,
          },
        }));
      })
      .catch((err: unknown) => {
        setData((previous) => ({ ...previous, [role]: EMPTY }));
        if (!(err instanceof ApiError && err.status === 404)) {
          setErrors((previous) => ({ ...previous, [role]: errorMessage(err) }));
        }
      })
      .finally(() => {
        setLoading((previous) => ({ ...previous, [role]: false }));
      });
  }, [role]);

  return { data, loading, errors };
}
