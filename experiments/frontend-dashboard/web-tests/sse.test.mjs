import assert from "node:assert/strict";
import test from "node:test";

import {
  StreamHttpError,
  consumeEventStream,
  nextRetryDelay,
  parseEventData,
  parseSSEFrames,
} from "../static/src/sse.mjs";

test("parseSSEFrames splits frames and keeps partial tail", () => {
  const first = parseSSEFrames(
    'id: 1\nevent: snapshot\ndata: {"a":1}\n\nid: 2\nevent: snapshot\ndata: {"b"',
  );
  assert.equal(first.frames.length, 1);
  assert.deepEqual(first.frames[0], {
    id: 1,
    event: "snapshot",
    data: '{"a":1}',
  });
  assert.equal(first.rest, 'id: 2\nevent: snapshot\ndata: {"b"');

  const second = parseSSEFrames(`${first.rest}:2}\n\n`);
  assert.equal(second.frames.length, 1);
  assert.deepEqual(second.frames[0], {
    id: 2,
    event: "snapshot",
    data: '{"b":2}',
  });
  assert.equal(second.rest, "");
});

test("parseSSEFrames handles CRLF and ignores comments", () => {
  const parsed = parseSSEFrames(": ping\r\n\r\nid: 7\r\nevent: snapshot\r\ndata: {}\r\n\r\n");
  assert.equal(parsed.frames.length, 1);
  assert.deepEqual(parsed.frames[0], { id: 7, event: "snapshot", data: "{}" });
});

test("parseEventData returns object or null", () => {
  assert.deepEqual(parseEventData({ data: '{"daemon":{}}' }), { daemon: {} });
  assert.equal(parseEventData({ data: "not-json" }), null);
  assert.equal(parseEventData({ data: "" }), null);
});

test("nextRetryDelay is exponential, capped and jittered", () => {
  const fixed = { random: () => 0.5 };
  assert.equal(nextRetryDelay(1, fixed), 1000);
  assert.equal(nextRetryDelay(2, fixed), 2000);
  assert.equal(nextRetryDelay(3, fixed), 4000);
  assert.equal(nextRetryDelay(10, fixed), 15000);
  assert.equal(nextRetryDelay(1, { random: () => 0 }), 800);
  assert.equal(nextRetryDelay(1, { random: () => 1 }), 1200);
});

function streamOf(chunks) {
  const encoder = new TextEncoder();
  return new ReadableStream({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(encoder.encode(chunk));
      controller.close();
    },
  });
}

test("consumeEventStream parses split chunks and dedupes by id", async () => {
  const frames = [];
  const states = [];
  const controller = new AbortController();
  await consumeEventStream({
    url: "/api/events",
    signal: controller.signal,
    fetchImpl: async () => ({
      ok: true,
      body: streamOf([
        'id: 1\nevent: snapshot\ndata: {"agents":[]}\n\nid: 2\nevent: he',
        'artbeat\ndata: {"agents":[]}\n\nid: 1\nevent: heartbeat\ndata: {{}\n\n',
      ]),
    }),
    onFrame: (frame) => frames.push(frame.event),
    onState: (state) => states.push(state),
    sleep: async () => controller.abort(),
  });
  assert.deepEqual(frames, ["snapshot", "heartbeat"]);
  assert.deepEqual(states, ["open", "closed"]);
});

test("consumeEventStream backs off and retries after failure", async () => {
  const states = [];
  const errors = [];
  const controller = new AbortController();
  let calls = 0;
  await consumeEventStream({
    url: "/api/events",
    signal: controller.signal,
    fetchImpl: async () => {
      calls += 1;
      if (calls === 1) return { ok: false, status: 503 };
      return { ok: true, body: streamOf(['id: 0\nevent: snapshot\ndata: {}\n\n']) };
    },
    onFrame: () => {},
    onState: (state) => states.push(state),
    onError: (error) => errors.push(error),
    retry: () => 0,
    sleep: async () => {
      if (calls >= 2) controller.abort();
    },
  });
  assert.equal(calls, 2);
  assert.deepEqual(states, ["error", "open", "closed"]);
  assert.equal(errors.length, 1);
  assert.ok(errors[0] instanceof StreamHttpError);
  assert.equal(errors[0].status, 503);
});
