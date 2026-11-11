import { parseSSEFrames } from "./sse";
import type { SSEFrame } from "./types";

export type ChatBlock =
  | { kind: "user"; text: string }
  | { kind: "thinking"; text: string }
  | { kind: "text"; text: string }
  | { kind: "tool"; name: string; input: string }
  | { kind: "tool_result"; text: string }
  | { kind: "meta"; text: string }
  | { kind: "error"; text: string };

export interface ChatEntry {
  id: number;
  block: ChatBlock;
}

export interface ChatTurnSummary {
  session_id?: string;
  resumed?: boolean;
  forked?: boolean;
  inherited_from?: string | null;
  result?: string;
  cost_usd?: number | null;
  disconnected?: boolean;
  error?: string;
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === "object" ? (value as Record<string, unknown>) : null;
}

function contentBlocks(event: Record<string, unknown>): Array<Record<string, unknown>> {
  const message = asRecord(event.message);
  const content = message?.content;
  if (!Array.isArray(content)) return [];
  return content.map(asRecord).filter((block): block is Record<string, unknown> => block !== null);
}

/** 把工具参数压成一行人类可读摘要，避免把原始 JSON 铺在界面上。 */
export function describeToolInput(name: string, input: unknown): string {
  const tool = name.toLowerCase();
  const record = asRecord(input);
  if (record) {
    const file = record.file_path ?? record.path;
    if (tool === "read" && typeof file === "string") return `读取 ${file}`;
    if ((tool === "edit" || tool === "write" || tool === "multiedit") && typeof file === "string") {
      return file;
    }
    const pattern = record.pattern ?? record.query;
    if (typeof pattern === "string") {
      const scope = typeof record.path === "string" ? ` · ${record.path}` : "";
      return `${pattern}${scope}`;
    }
    const command = record.command;
    if (typeof command === "string") return command.slice(0, 120);
    const url = record.url;
    if (typeof url === "string") return url;
    const first = Object.values(record).find((value) => typeof value === "string");
    if (typeof first === "string") return first.slice(0, 120);
  }
  try {
    return JSON.stringify(input ?? {}).slice(0, 160);
  } catch {
    return "";
  }
}

/** 把 claude stream-json 事件映射为 UI 块；不按顶层 type 直接分类。 */
export function mapChatEvent(event: unknown): ChatBlock[] {
  const record = asRecord(event);
  if (!record) return [];
  const type = record.type;

  if (type === "system") {
    return [];
  }

  if (type === "assistant") {
    const blocks: ChatBlock[] = [];
    for (const block of contentBlocks(record)) {
      if (block.type === "thinking") {
        const text = typeof block.thinking === "string" ? block.thinking : "";
        if (text) blocks.push({ kind: "thinking", text });
      } else if (block.type === "text") {
        const text = typeof block.text === "string" ? block.text : "";
        if (text) blocks.push({ kind: "text", text });
      } else if (block.type === "tool_use") {
        const name = typeof block.name === "string" ? block.name : "tool";
        blocks.push({ kind: "tool", name, input: describeToolInput(name, block.input) });
      }
    }
    return blocks;
  }

  if (type === "user") {
    return [];
  }

  if (type === "result") {
    return [{ kind: "meta", text: "本轮完成" }];
  }

  return [];
}

/** 解析一帧聊天 SSE：message 返回 UI 块，end 返回本轮摘要。 */
export function mapChatFrame(frame: SSEFrame): { blocks: ChatBlock[]; summary?: ChatTurnSummary } {
  if (frame.event === "end") {
    try {
      const parsed: unknown = JSON.parse(frame.data);
      const record = asRecord(parsed);
      return record ? { blocks: [], summary: record as ChatTurnSummary } : { blocks: [] };
    } catch {
      return { blocks: [] };
    }
  }
  if (frame.event !== "message") return { blocks: [] };
  try {
    return { blocks: mapChatEvent(JSON.parse(frame.data)) };
  } catch {
    return { blocks: [] };
  }
}

/** 读取 POST /chat 的 SSE 响应；不做断线重连（对话不应自动重放）。 */
export async function consumeChatStream(
  response: Response,
  handlers: { onBlocks: (blocks: ChatBlock[]) => void; onEnd: (summary: ChatTurnSummary) => void },
): Promise<void> {
  if (!response.body) throw new Error("empty chat stream");
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
        const mapped = mapChatFrame(frame);
        if (mapped.blocks.length > 0) handlers.onBlocks(mapped.blocks);
        if (mapped.summary) handlers.onEnd(mapped.summary);
      }
    }
  } finally {
    try {
      reader.releaseLock();
    } catch {
      // 已释放，忽略
    }
  }
}
