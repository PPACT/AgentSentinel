// @vitest-environment jsdom
import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { useChatSessions } from "./useChatSessions";

const backend = vi.hoisted(() => ({ startChatStream: vi.fn() }));

vi.mock("@/lib/backend", () => ({
  startChatStream: backend.startChatStream,
}));

function streamOf(chunks: string[]): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder();
  return new ReadableStream({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(encoder.encode(chunk));
      controller.close();
    },
  });
}

function reply(role: string, summary: Record<string, unknown> = { resumed: false }): Response {
  const event = JSON.stringify({
    type: "assistant",
    message: { content: [{ type: "text", text: `${role} reply` }] },
  });
  const result = JSON.stringify({ type: "result", subtype: "success" });
  return {
    body: streamOf([
      `event: message\ndata: ${event}\n\n`,
      `event: message\ndata: ${result}\n\n`,
      `event: end\ndata: ${JSON.stringify(summary)}\n\n`,
    ]),
  } as Response;
}

afterEach(() => {
  backend.startChatStream.mockReset();
});

describe("useChatSessions", () => {
  it("keeps histories isolated per role", async () => {
    backend.startChatStream.mockImplementation(async ({ role }: { role: string }) => reply(role));
    const { result } = renderHook(() => useChatSessions());

    await act(async () => {
      await Promise.all([result.current.send("dev", "dev prompt"), result.current.send("doc", "doc prompt")]);
    });

    expect(backend.startChatStream).toHaveBeenCalledTimes(2);
    expect(result.current.histories.dev?.map((item) => item.block.kind)).toEqual([
      "user",
      "text",
      "meta",
    ]);
    expect(result.current.histories.doc?.map((item) => item.block.kind)).toEqual([
      "user",
      "text",
      "meta",
    ]);
    expect(JSON.stringify(result.current.histories.dev)).toContain("dev reply");
    expect(JSON.stringify(result.current.histories.doc)).toContain("doc reply");
  });

  it("sends only one POST per role even when called repeatedly", async () => {
    backend.startChatStream.mockImplementation(async ({ role }: { role: string }) => reply(role));
    const { result } = renderHook(() => useChatSessions());

    await act(async () => {
      const first = result.current.send("dev", "only once");
      const second = result.current.send("dev", "only once");
      const third = result.current.send("dev", "only once");
      await Promise.all([first, second, third]);
    });

    expect(backend.startChatStream).toHaveBeenCalledTimes(1);
  });

  it("records the inherited external session for the role", async () => {
    backend.startChatStream.mockImplementation(async ({ role }: { role: string }) =>
      reply(role, { session_id: "forked-1", resumed: true, inherited_from: "external-1" }),
    );
    const { result } = renderHook(() => useChatSessions());

    await act(async () => {
      await result.current.send("dev", "继承");
    });

    expect(result.current.inherited.dev).toBe("external-1");
  });

  it("aborts the active request when stop is requested", async () => {
    let signal: AbortSignal | undefined;
    backend.startChatStream.mockImplementation(
      ({ signal: aborted }: { signal: AbortSignal }) =>
        new Promise((_resolve, reject) => {
          signal = aborted;
          aborted.addEventListener("abort", () =>
            reject(new DOMException("aborted", "AbortError")),
          );
        }),
    );
    const { result } = renderHook(() => useChatSessions());

    let pending: Promise<void> | undefined;
    act(() => {
      pending = result.current.send("dev", "long task");
    });
    await waitFor(() => expect(signal).toBeDefined());

    act(() => {
      result.current.stop("dev");
    });
    await act(async () => {
      await pending;
    });

    expect(signal?.aborted).toBe(true);
    expect(result.current.busy.dev).toBe(false);
  });
});
