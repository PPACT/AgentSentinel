// @vitest-environment jsdom
import { renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { useTerminalHistory } from "./useTerminalHistory";

const backend = vi.hoisted(() => {
  class FakeApiError extends Error {
    status: number;
    constructor(status: number, message: string) {
      super(message);
      this.status = status;
    }
  }
  return { getSessionHistory: vi.fn(), FakeApiError };
});

vi.mock("@/lib/backend", () => ({
  getSessionHistory: backend.getSessionHistory,
  ApiError: backend.FakeApiError,
}));

afterEach(() => {
  backend.getSessionHistory.mockReset();
});

describe("useTerminalHistory", () => {
  it("loads and maps terminal messages once per role", async () => {
    backend.getSessionHistory.mockResolvedValue({
      role: "dev",
      session_id: "s1",
      source: "terminal",
      messages: [
        { role: "user", blocks: [{ kind: "text", text: "终端问题" }] },
        {
          role: "assistant",
          blocks: [
            { kind: "thinking", text: "内部" },
            { kind: "tool", name: "Read", input: "读取 a.py" },
            { kind: "text", text: "终端回答" },
          ],
        },
      ],
      truncated: false,
      context_tokens: 195000,
      bytes: 1024,
    });

    const { result } = renderHook(() => useTerminalHistory("dev"));

    await waitFor(() => expect(result.current.data.dev?.entries).toHaveLength(4));
    expect(result.current.data.dev?.contextTokens).toBe(195000);
    expect(result.current.data.dev?.entries[3].block).toEqual({ kind: "text", text: "终端回答" });
  });

  it("treats 404 as an empty history without surfacing an error", async () => {
    backend.getSessionHistory.mockRejectedValue(new backend.FakeApiError(404, "no session"));

    const { result } = renderHook(() => useTerminalHistory("doc"));

    await waitFor(() => expect(result.current.loading.doc).toBe(false));
    expect(result.current.data.doc?.entries).toEqual([]);
    expect(result.current.errors.doc).toBeUndefined();
  });
});
