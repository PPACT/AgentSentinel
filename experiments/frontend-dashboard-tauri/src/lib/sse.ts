import type { SSEFrame } from "./types";

export class StreamHttpError extends Error {
  status: number;

  constructor(status: number) {
    super(`HTTP ${status}`);
    this.name = "StreamHttpError";
    this.status = status;
  }
}

/** 解析 SSE 缓冲区，返回完整帧与未完成的剩余文本；注释行（: ping）忽略。 */
export function parseSSEFrames(buffer: string): { frames: SSEFrame[]; rest: string } {
  const normalized = buffer.replace(/\r\n/g, "\n");
  const parts = normalized.split("\n\n");
  const rest = parts.pop() ?? "";
  const frames: SSEFrame[] = [];
  for (const raw of parts) {
    const frame: SSEFrame = { id: null, event: "", data: "" };
    const dataLines: string[] = [];
    for (const line of raw.split("\n")) {
      if (!line || line.startsWith(":")) continue;
      const colon = line.indexOf(":");
      const field = colon === -1 ? line : line.slice(0, colon);
      let value = colon === -1 ? "" : line.slice(colon + 1);
      if (value.startsWith(" ")) value = value.slice(1);
      if (field === "id") {
        const parsed = Number.parseInt(value, 10);
        frame.id = Number.isNaN(parsed) ? null : parsed;
      } else if (field === "event") {
        frame.event = value;
      } else if (field === "data") {
        dataLines.push(value);
      }
    }
    if (!frame.event) continue;
    frame.data = dataLines.join("\n");
    frames.push(frame);
  }
  return { frames, rest };
}

export function parseEventData<T = unknown>(frame: SSEFrame): T | null {
  const raw = frame.data ?? "";
  if (!raw) return null;
  try {
    const parsed: unknown = JSON.parse(raw);
    return parsed && typeof parsed === "object" ? (parsed as T) : null;
  } catch {
    return null;
  }
}

/** 指数退避：1s 起，上限 15s，±20% 抖动。attempt 从 1 开始。 */
export function nextRetryDelay(
  attempt: number,
  options: { base?: number; max?: number; jitter?: number; random?: () => number } = {},
): number {
  const { base = 1000, max = 15000, jitter = 0.2, random = Math.random } = options;
  const exponential = Math.min(max, base * 2 ** Math.max(0, attempt - 1));
  const factor = 1 - jitter + random() * jitter * 2;
  return Math.max(0, Math.round(exponential * factor));
}

export interface EventStreamOptions {
  fetchImpl: (url: string, init: RequestInit) => Promise<Response>;
  /** 固定 URL 或每次重连时重新解析（daemon 重启可能换端口）。 */
  url: string | (() => string | Promise<string>);
  signal: AbortSignal;
  onFrame: (frame: SSEFrame) => void;
  onState: (state: "open" | "closed" | "error") => void;
  onError?: (error: unknown) => void;
  retry?: (attempt: number) => number;
  sleep?: (ms: number) => Promise<void>;
  headers?: () => Promise<Record<string, string>>;
}

const defaultSleep = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));

/** fetch + ReadableStream 读 SSE；断开后指数退避重连；不使用 EventSource/setInterval。 */
export async function consumeEventStream(options: EventStreamOptions): Promise<void> {
  const {
    fetchImpl,
    url,
    signal,
    onFrame,
    onState,
    onError = () => {},
    retry = nextRetryDelay,
    sleep = defaultSleep,
    headers = async () => ({}),
  } = options;
  let attempt = 0;
  while (!signal.aborted) {
    let reader: ReadableStreamDefaultReader<Uint8Array> | null = null;
    try {
      const streamUrl = typeof url === "function" ? await url() : url;
      const response = await fetchImpl(streamUrl, {
        headers: { Accept: "text/event-stream", ...(await headers()) },
        signal,
      });
      if (!response.ok) throw new StreamHttpError(response.status);
      onState("open");
      attempt = 0;
      if (!response.body) throw new Error("empty response body");
      reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      let lastId: number | null = null;
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const parsed = parseSSEFrames(buffer);
        buffer = parsed.rest;
        for (const frame of parsed.frames) {
          if (frame.id !== null) {
            if (lastId !== null && frame.id <= lastId) continue;
            lastId = frame.id;
          }
          onFrame(frame);
        }
      }
      onState("closed");
    } catch (error) {
      if (signal.aborted) return;
      onState("error");
      onError(error);
    } finally {
      if (reader) {
        try {
          reader.releaseLock();
        } catch {
          // 已释放/已取消，忽略
        }
      }
    }
    attempt += 1;
    await sleep(retry(attempt));
  }
}
