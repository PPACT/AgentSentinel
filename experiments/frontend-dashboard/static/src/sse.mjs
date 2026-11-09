// SSE 帧解析、退避重连与读取循环（浏览器 / Node 共用，无第三方依赖）。

export class StreamHttpError extends Error {
  constructor(status) {
    super(`HTTP ${status}`);
    this.name = "StreamHttpError";
    this.status = status;
  }
}

/**
 * 解析一个 SSE 文本缓冲区，返回完整帧与未完成的剩余文本。
 * 帧形状：{ id: number|null, event: string, data: string }；注释行（: ping）忽略。
 */
export function parseSSEFrames(buffer) {
  const normalized = buffer.replace(/\r\n/g, "\n");
  const parts = normalized.split("\n\n");
  const rest = parts.pop() ?? "";
  const frames = [];
  for (const raw of parts) {
    const frame = { id: null, event: "", data: "" };
    const dataLines = [];
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

export function parseEventData(frame) {
  const raw = frame?.data ?? "";
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw);
    return parsed && typeof parsed === "object" ? parsed : null;
  } catch {
    return null;
  }
}

/** 指数退避：1s 起，上限 15s，带 ±20% 抖动。attempt 从 1 开始。 */
export function nextRetryDelay(attempt, options = {}) {
  const { base = 1000, max = 15000, jitter = 0.2, random = Math.random } = options;
  const exponential = Math.min(max, base * 2 ** Math.max(0, attempt - 1));
  const factor = 1 - jitter + random() * jitter * 2;
  return Math.max(0, Math.round(exponential * factor));
}

export const defaultSleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

/**
 * 用 fetch + ReadableStream 读取 SSE；断开后指数退避重连。
 * 不轮询、不使用 EventSource；每次重连重置 id 去重游标（daemon 重启后 revision 归零）。
 */
export async function consumeEventStream(options) {
  const {
    url,
    fetchImpl = globalThis.fetch,
    signal,
    onFrame,
    onState = () => {},
    onError = () => {},
    retry = nextRetryDelay,
    sleep = defaultSleep,
  } = options;
  let attempt = 0;
  while (!signal?.aborted) {
    let reader = null;
    try {
      const response = await fetchImpl(url, {
        headers: { Accept: "text/event-stream" },
        signal,
      });
      if (!response.ok) throw new StreamHttpError(response.status);
      onState("open");
      attempt = 0;
      const stream = response.body;
      if (!stream) throw new Error("empty response body");
      reader = stream.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      let lastId = null;
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
      if (signal?.aborted) return;
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
