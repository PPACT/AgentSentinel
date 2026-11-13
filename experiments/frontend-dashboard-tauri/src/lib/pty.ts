import { parseSSEFrames } from "./sse";
import type { ChatBlock } from "./chat";
import type { PtyNotice, TokenUsage } from "./types";

function parseNotice(raw: string): PtyNotice | null {
  try {
    const payload = JSON.parse(raw) as Partial<PtyNotice>;
    if (typeof payload.message !== "string") return null;
    return {
      seq: typeof payload.seq === "number" ? payload.seq : 0,
      kind: typeof payload.kind === "string" ? payload.kind : "notice",
      message: payload.message,
      session_id: typeof payload.session_id === "string" ? payload.session_id : undefined,
    };
  } catch {
    return null;
  }
}

export interface PtyChunk {
  seq: number;
  data: Uint8Array;
}

function decodeBase64(value: string): Uint8Array {
  const binary = atob(value);
  const bytes = new Uint8Array(binary.length);
  for (let index = 0; index < binary.length; index += 1) {
    bytes[index] = binary.charCodeAt(index);
  }
  return bytes;
}

export function decodePtyFrame(event: string, raw: string): PtyChunk | null {
  if (event !== "output") return null;
  try {
    const payload = JSON.parse(raw) as { seq?: unknown; data?: unknown };
    if (typeof payload.data !== "string") return null;
    return {
      seq: typeof payload.seq === "number" ? payload.seq : 0,
      data: decodeBase64(payload.data),
    };
  } catch {
    return null;
  }
}

/** 读取 /pty/stream：base64 字节帧 → Uint8Array；exit 事件结束。 */
export async function consumePtyStream(
  response: Response,
  handlers: {
    onData: (chunk: Uint8Array) => void;
    onExit: (status: number | null) => void;
    onNotice?: (notice: PtyNotice) => void;
  },
): Promise<void> {
  if (!response.body) throw new Error("empty pty stream");
  await consumeSse(response, (frame) => {
    if (frame.event === "output") {
      const chunk = decodePtyFrame(frame.event, frame.data);
      if (chunk) handlers.onData(chunk.data);
    } else if (frame.event === "notice") {
      const notice = parseNotice(frame.data);
      if (notice) handlers.onNotice?.(notice);
    } else if (frame.event === "exit") {
      let status: number | null = null;
      try {
        const payload = JSON.parse(frame.data) as { exit_status?: unknown };
        status = typeof payload.exit_status === "number" ? payload.exit_status : null;
      } catch {
        status = null;
      }
      handlers.onExit(status);
    }
  });
}

async function consumeSse(
  response: Response,
  onFrame: (frame: { event: string; data: string }) => void,
): Promise<void> {
  if (!response.body) throw new Error("empty stream");
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const parsed = parseSSEFrames(buffer);
      buffer = parsed.rest;
      for (const frame of parsed.frames) onFrame(frame);
    }
  } finally {
    try {
      reader.releaseLock();
    } catch {
      // 已释放
    }
  }
}

export interface PtyChatMessage {
  role: string;
  blocks: ChatBlock[];
  timestamp?: string | null;
}

/** 读取 /pty/chat/stream 的 JSONL 旁路事件。 */
export async function consumePtyChatStream(
  response: Response,
  handlers: {
    onMessages: (messages: PtyChatMessage[]) => void;
    onContext: (tokens: number) => void;
    onUsage?: (usage: TokenUsage) => void;
    onNotice?: (notice: PtyNotice) => void;
    onSession: (sessionId: string) => void;
    onExit: () => void;
  },
): Promise<void> {
  await consumeSse(response, (frame) => {
    if (frame.event === "message") {
      try {
        const payload = JSON.parse(frame.data) as { message?: PtyChatMessage };
        if (payload.message && Array.isArray(payload.message.blocks)) {
          handlers.onMessages([payload.message]);
        }
      } catch {
        // 忽略坏帧
      }
    } else if (frame.event === "usage") {
      try {
        const payload = JSON.parse(frame.data) as { usage?: Partial<TokenUsage> };
        if (payload.usage && handlers.onUsage) {
          handlers.onUsage({
            input_tokens: Number(payload.usage.input_tokens) || 0,
            output_tokens: Number(payload.usage.output_tokens) || 0,
            cache_read_input_tokens: Number(payload.usage.cache_read_input_tokens) || 0,
            cache_creation_input_tokens: Number(payload.usage.cache_creation_input_tokens) || 0,
          });
        }
      } catch {
        // 忽略
      }
    } else if (frame.event === "context") {
      try {
        const payload = JSON.parse(frame.data) as { context_tokens?: unknown };
        if (typeof payload.context_tokens === "number") handlers.onContext(payload.context_tokens);
      } catch {
        // 忽略
      }
    } else if (frame.event === "notice") {
      const notice = parseNotice(frame.data);
      if (notice) handlers.onNotice?.(notice);
    } else if (frame.event === "session") {
      try {
        const payload = JSON.parse(frame.data) as { session_id?: unknown };
        if (typeof payload.session_id === "string") handlers.onSession(payload.session_id);
      } catch {
        // 忽略
      }
    } else if (frame.event === "exit") {
      handlers.onExit();
    }
  });
}
