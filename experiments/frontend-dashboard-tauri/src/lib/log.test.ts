import { describe, expect, it } from "vitest";

import {
  appendBounded,
  availableRoles,
  CLIENT_ROLE,
  entryRole,
  filterLogs,
  logEventText,
  makeLogEntry,
} from "./log";

describe("makeLogEntry", () => {
  it("formats time and keeps level/text", () => {
    const entry = makeLogEntry(7, "warn", "连接中断", new Date(2026, 10, 9, 9, 5, 3));
    expect(entry).toEqual({ id: 7, time: "09:05:03", level: "warn", text: "连接中断" });
  });
});

describe("appendBounded", () => {
  it("keeps only the latest entries", () => {
    const logs = [makeLogEntry(1, "info", "a"), makeLogEntry(2, "info", "b")];
    const next = appendBounded(logs, makeLogEntry(3, "info", "c"), 2);
    expect(next.map((entry) => entry.id)).toEqual([2, 3]);
  });
});

describe("logEventText", () => {
  it("prefixes the role and falls back to log", () => {
    expect(logEventText("dev", "started")).toBe("[dev] started");
    expect(logEventText("", "line")).toBe("[log] line");
    expect(logEventText(undefined, "line")).toBe("[log] line");
  });
});

describe("role filtering", () => {
  const logs = [
    makeLogEntry(1, "info", "[dev] started", new Date(), "dev"),
    makeLogEntry(2, "warn", "[doc] slow", new Date(), "doc"),
    makeLogEntry(3, "info", "SSE 已连接"),
  ];

  it("derives roles and client fallback", () => {
    expect(entryRole(logs[0]!)).toBe("dev");
    expect(entryRole(logs[2]!)).toBe(CLIENT_ROLE);
    expect(availableRoles(logs)).toEqual([CLIENT_ROLE, "dev", "doc"]);
  });

  it("filters by role and case-insensitive query", () => {
    expect(filterLogs(logs, "dev", "").map((entry) => entry.id)).toEqual([1]);
    expect(filterLogs(logs, "", "SLOW").map((entry) => entry.id)).toEqual([2]);
    expect(filterLogs(logs, CLIENT_ROLE, "sse").map((entry) => entry.id)).toEqual([3]);
    expect(filterLogs(logs, "dev", "sse")).toEqual([]);
  });
});
