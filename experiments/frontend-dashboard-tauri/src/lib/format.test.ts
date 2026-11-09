import { describe, expect, it } from "vitest";

import {
  connectionLabel,
  daemonSummary,
  errorMessage,
  formatAgent,
  formatTask,
  isDaemonDown,
  localTimestamp,
  shortTimestamp,
} from "./format";

describe("formatAgent", () => {
  it("fills contracts and empty placeholders", () => {
    expect(
      formatAgent({
        role: "dev",
        pid: 1234,
        pgid: 1234,
        status: "registered",
        alive: true,
        last_heartbeat: "2026-11-09T00:00:00Z",
        current_task_id: "",
      }),
    ).toEqual({
      role: "dev",
      pid: "1234",
      pgid: "1234",
      status: "registered",
      alive: "在线",
      lastHeartbeat: "2026-11-09T00:00:00Z",
      currentTask: "—",
    });
    expect(formatAgent({ alive: false }).alive).toBe("已停止");
    expect(formatAgent(null).role).toBe("—");
  });
});

describe("formatTask", () => {
  it("fills contracts", () => {
    expect(
      formatTask({
        task_id: "T-1",
        from_role: "doc",
        to_role: "dev",
        intent: "implement",
        status: "running",
        updated_at: "2026-11-09T00:00:00Z",
      }),
    ).toEqual({
      taskId: "T-1",
      fromRole: "doc",
      toRole: "dev",
      intent: "implement",
      status: "running",
      updatedAt: "2026-11-09T00:00:00Z",
    });
  });
});

describe("daemonSummary", () => {
  it("handles missing daemon", () => {
    expect(daemonSummary(null)).toBe("中控未启动");
    expect(daemonSummary({ pid: 9, host: "127.0.0.1", port: 8954, started_at: "t0" })).toBe(
      "pid 9 · 127.0.0.1:8954 · 启动于 t0",
    );
  });
});

describe("localTimestamp", () => {
  it("formats local time as YYYY-MM-DD HH:mm:ss", () => {
    const at = new Date(2026, 10, 9, 9, 5, 3);
    expect(localTimestamp(at.toISOString())).toBe("2026-11-09 09:05:03");
  });

  it("returns raw text or placeholder when unparsable", () => {
    expect(localTimestamp("not-a-date")).toBe("not-a-date");
    expect(localTimestamp(null)).toBe("—");
    expect(localTimestamp("")).toBe("—");
  });
});

describe("shortTimestamp", () => {
  it("formats to local MM-DD HH:mm:ss and passes through invalid values", () => {
    expect(shortTimestamp(new Date(2026, 10, 9, 9, 5, 3).toISOString())).toBe("11-09 09:05:03");
    expect(shortTimestamp("not-a-date")).toBe("not-a-date");
    expect(shortTimestamp(null)).toBe("—");
  });
});

describe("connectionLabel", () => {
  it("maps states", () => {
    expect(connectionLabel("open")).toBe("已连接（SSE）");
    expect(connectionLabel("error")).toBe("连接中断，正在重连…");
  });
});

describe("errorMessage", () => {
  it("gives readable 401/503 hints", () => {
    expect(errorMessage({ status: 401 })).toMatch(/未授权（401）/);
    expect(errorMessage({ status: 503 })).toMatch(/中控未启动/);
    expect(errorMessage(new Error("boom"))).toMatch(/boom/);
    expect(isDaemonDown({ status: 503 })).toBe(true);
    expect(isDaemonDown(new Error("boom"))).toBe(false);
  });
});
