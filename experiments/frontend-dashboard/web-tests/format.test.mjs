import assert from "node:assert/strict";
import test from "node:test";

import {
  connectionLabel,
  daemonSummary,
  errorMessage,
  formatAgent,
  formatTask,
  isDaemonDown,
} from "../static/src/format.mjs";

test("formatAgent fills contracts and empty placeholders", () => {
  assert.deepEqual(
    formatAgent({
      role: "dev",
      pid: 1234,
      pgid: 1234,
      status: "registered",
      alive: true,
      last_heartbeat: "2026-11-09T00:00:00Z",
      current_task_id: "",
    }),
    {
      role: "dev",
      pid: "1234",
      pgid: "1234",
      status: "registered",
      alive: "在线",
      lastHeartbeat: "2026-11-09T00:00:00Z",
      currentTask: "—",
    },
  );
  assert.equal(formatAgent({ alive: false }).alive, "已停止");
  assert.equal(formatAgent(null).role, "—");
});

test("formatTask fills contracts", () => {
  assert.deepEqual(
    formatTask({
      task_id: "T-1",
      from_role: "doc",
      to_role: "dev",
      intent: "implement",
      status: "running",
      updated_at: "2026-11-09T00:00:00Z",
    }),
    {
      taskId: "T-1",
      fromRole: "doc",
      toRole: "dev",
      intent: "implement",
      status: "running",
      updatedAt: "2026-11-09T00:00:00Z",
    },
  );
});

test("daemonSummary handles missing daemon", () => {
  assert.equal(daemonSummary(null), "中控未启动");
  assert.equal(
    daemonSummary({ pid: 9, host: "127.0.0.1", port: 8954, started_at: "t0" }),
    "pid 9 · 127.0.0.1:8954 · 启动于 t0",
  );
});

test("connectionLabel maps states", () => {
  assert.equal(connectionLabel("open"), "已连接（SSE）");
  assert.equal(connectionLabel("error"), "连接中断，正在重连…");
  assert.equal(connectionLabel("unknown"), "未知状态");
});

test("errorMessage gives readable 401/503 hints", () => {
  assert.match(errorMessage({ status: 401 }), /未授权（401）/);
  assert.match(errorMessage({ status: 503 }), /中控未启动/);
  assert.match(errorMessage(new Error("boom")), /boom/);
  assert.equal(isDaemonDown({ status: 503 }), true);
  assert.equal(isDaemonDown(new Error("boom")), false);
});
