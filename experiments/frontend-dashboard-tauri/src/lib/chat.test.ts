import { describe, expect, it } from "vitest";

import { consumeChatStream, describeToolInput, mapChatEvent, mapChatFrame } from "./chat";

function streamOf(chunks: string[]): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder();
  return new ReadableStream({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(encoder.encode(chunk));
      controller.close();
    },
  });
}

describe("mapChatEvent", () => {
  it("maps assistant content blocks instead of top-level type", () => {
    const blocks = mapChatEvent({
      type: "assistant",
      message: {
        content: [
          { type: "thinking", thinking: "先看一下" },
          { type: "tool_use", name: "Read", input: { file_path: "a.py" } },
          { type: "text", text: "答案" },
        ],
      },
    });

    expect(blocks.map((block) => block.kind)).toEqual(["thinking", "tool", "text"]);
    expect(blocks[1]).toEqual({ kind: "tool", name: "Read", input: "读取 a.py" });
    expect(blocks[2]).toEqual({ kind: "text", text: "答案" });
  });

  it("summarizes tool inputs for low-noise display", () => {
    expect(describeToolInput("Read", { file_path: "src/App.tsx" })).toBe("读取 src/App.tsx");
    expect(describeToolInput("Grep", { pattern: "useEffect", path: "src" })).toBe("useEffect · src");
    expect(describeToolInput("Bash", { command: "npm test" })).toBe("npm test");
    expect(describeToolInput("Write", { file_path: "a.txt", content: "x" })).toBe("a.txt");
  });

  it("hides tool_result/system and reduces result to one line", () => {
    expect(
      mapChatEvent({
        type: "user",
        message: { content: [{ type: "tool_result", content: "ok" }] },
      }),
    ).toEqual([]);
    expect(mapChatEvent({ type: "system", session_id: "s1", model: "m" })).toEqual([]);
    expect(mapChatEvent({ type: "result", subtype: "success", total_cost_usd: 0.01 })).toEqual([
      { kind: "meta", text: "本轮完成" },
    ]);
    expect(mapChatEvent({ type: "unknown" })).toEqual([]);
  });
});

describe("consumeChatStream", () => {
  it("emits blocks and the end summary", async () => {
    const frames = [
      'event: message\ndata: {"type":"assistant","message":{"content":[{"type":"text","text":"你好"}]}}\n\n',
      'event: end\ndata: {"session_id":"s1","resumed":false,"cost_usd":0.02}\n\n',
    ];
    const blocks: string[] = [];
    let summary: { session_id?: string } | null = null;

    await consumeChatStream({ body: streamOf(frames) } as Response, {
      onBlocks: (items) => blocks.push(...items.map((item) => item.kind)),
      onEnd: (value) => {
        summary = value;
      },
    });

    expect(blocks).toEqual(["text"]);
    expect(summary).toEqual({ session_id: "s1", resumed: false, cost_usd: 0.02 });
  });
});

describe("mapChatFrame", () => {
  it("ignores malformed JSON", () => {
    expect(mapChatFrame({ id: null, event: "message", data: "not-json" })).toEqual({ blocks: [] });
  });
});
