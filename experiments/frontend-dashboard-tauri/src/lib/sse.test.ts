import { describe, expect, it } from "vitest";

import {
  StreamHttpError,
  consumeEventStream,
  nextRetryDelay,
  parseEventData,
  parseSSEFrames,
} from "./sse";

function streamOf(chunks: string[]): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder();
  return new ReadableStream({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(encoder.encode(chunk));
      controller.close();
    },
  });
}

describe("parseSSEFrames", () => {
  it("splits frames and keeps partial tail", () => {
    const first = parseSSEFrames(
      'id: 1\nevent: snapshot\ndata: {"a":1}\n\nid: 2\nevent: snapshot\ndata: {"b"',
    );
    expect(first.frames).toHaveLength(1);
    expect(first.frames[0]).toEqual({ id: 1, event: "snapshot", data: '{"a":1}' });
    expect(first.rest).toBe('id: 2\nevent: snapshot\ndata: {"b"');

    const second = parseSSEFrames(`${first.rest}:2}\n\n`);
    expect(second.frames).toHaveLength(1);
    expect(second.frames[0]).toEqual({ id: 2, event: "snapshot", data: '{"b":2}' });
    expect(second.rest).toBe("");
  });

  it("handles CRLF and ignores comments", () => {
    const parsed = parseSSEFrames(": ping\r\n\r\nid: 7\r\nevent: snapshot\r\ndata: {}\r\n\r\n");
    expect(parsed.frames).toEqual([{ id: 7, event: "snapshot", data: "{}" }]);
  });
});

describe("parseEventData", () => {
  it("returns object or null", () => {
    expect(parseEventData({ id: null, event: "x", data: '{"daemon":{}}' })).toEqual({ daemon: {} });
    expect(parseEventData({ id: null, event: "x", data: "not-json" })).toBeNull();
    expect(parseEventData({ id: null, event: "x", data: "" })).toBeNull();
  });
});

describe("nextRetryDelay", () => {
  it("is exponential, capped and jittered", () => {
    const fixed = { random: () => 0.5 };
    expect(nextRetryDelay(1, fixed)).toBe(1000);
    expect(nextRetryDelay(2, fixed)).toBe(2000);
    expect(nextRetryDelay(3, fixed)).toBe(4000);
    expect(nextRetryDelay(10, fixed)).toBe(15000);
    expect(nextRetryDelay(1, { random: () => 0 })).toBe(800);
    expect(nextRetryDelay(1, { random: () => 1 })).toBe(1200);
  });
});

describe("consumeEventStream", () => {
  it("parses split chunks and dedupes by id", async () => {
    const frames: string[] = [];
    const states: string[] = [];
    const controller = new AbortController();
    await consumeEventStream({
      url: "/api/events",
      signal: controller.signal,
      fetchImpl: async () => ({
        ok: true,
        status: 200,
        body: streamOf([
          'id: 1\nevent: snapshot\ndata: {"agents":[]}\n\nid: 2\nevent: he',
          'artbeat\ndata: {"agents":[]}\n\nid: 1\nevent: heartbeat\ndata: {}\n\n',
        ]),
      }) as unknown as Response,
      onFrame: (frame) => frames.push(frame.event),
      onState: (state) => states.push(state),
      sleep: async () => controller.abort(),
    });
    expect(frames).toEqual(["snapshot", "heartbeat"]);
    expect(states).toEqual(["open", "closed"]);
  });

  it("backs off and retries after failure", async () => {
    const states: string[] = [];
    const errors: unknown[] = [];
    const controller = new AbortController();
    let calls = 0;
    await consumeEventStream({
      url: "/api/events",
      signal: controller.signal,
      fetchImpl: async () => {
        calls += 1;
        if (calls === 1) return { ok: false, status: 503 } as unknown as Response;
        return {
          ok: true,
          status: 200,
          body: streamOf(['id: 0\nevent: snapshot\ndata: {}\n\n']),
        } as unknown as Response;
      },
      onFrame: () => {},
      onState: (state) => states.push(state),
      onError: (error) => errors.push(error),
      retry: () => 0,
      sleep: async () => {
        if (calls >= 2) controller.abort();
      },
    });
    expect(calls).toBe(2);
    expect(states).toEqual(["error", "open", "closed"]);
    expect(errors).toHaveLength(1);
    expect(errors[0]).toBeInstanceOf(StreamHttpError);
    expect((errors[0] as StreamHttpError).status).toBe(503);
  });
});
