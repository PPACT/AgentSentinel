import { parseSSEFrames } from "./sse";

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
  },
): Promise<void> {
  if (!response.body) throw new Error("empty pty stream");
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
      for (const frame of parsed.frames) {
        if (frame.event === "output") {
          const chunk = decodePtyFrame(frame.event, frame.data);
          if (chunk) handlers.onData(chunk.data);
        } else if (frame.event === "exit") {
          let status: number | null = null;
          try {
            const payload = JSON.parse(frame.data) as { exit_status?: unknown };
            status = typeof payload.exit_status === "number" ? payload.exit_status : null;
          } catch {
            status = null;
          }
          handlers.onExit(status);
          return;
        }
      }
    }
  } finally {
    try {
      reader.releaseLock();
    } catch {
      // 已释放
    }
  }
}
