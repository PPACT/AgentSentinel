import { describe, expect, it } from "vitest";

import { isCleanupCandidate } from "./wild";

describe("isCleanupCandidate", () => {
  it("accepts node/python executables with create_time", () => {
    expect(
      isCleanupCandidate({
        pid: 1,
        create_time: 100,
        cmdline: "C:\\Program Files\\nodejs\\node.exe server.js",
      }),
    ).toBe(true);
    expect(
      isCleanupCandidate({ pid: 2, create_time: 100, cmdline: "C:\\Python312\\python.exe agent.py" }),
    ).toBe(true);
    expect(isCleanupCandidate({ pid: 3, create_time: 100, cmdline: "pythonw.exe tool.py" })).toBe(
      true,
    );
  });

  it("rejects other executables, bare names, and missing create_time", () => {
    expect(isCleanupCandidate({ pid: 4, create_time: 100, cmdline: "claude.exe -p task" })).toBe(
      false,
    );
    expect(isCleanupCandidate({ pid: 5, create_time: 100, cmdline: "python agent.py" })).toBe(
      false,
    );
    expect(isCleanupCandidate({ pid: 6, cmdline: "node.exe server.js" })).toBe(false);
    expect(isCleanupCandidate({ pid: 7, create_time: 0, cmdline: "node.exe server.js" })).toBe(
      false,
    );
  });
});
