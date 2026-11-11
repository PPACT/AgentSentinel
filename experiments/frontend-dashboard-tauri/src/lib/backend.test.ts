import { describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  fetch: vi.fn(),
  create: vi.fn(),
}));

vi.mock("@tauri-apps/api/path", () => ({
  join: vi.fn(async (...parts: string[]) => parts.join("\\")),
}));
vi.mock("@tauri-apps/plugin-fs", () => ({
  readTextFile: vi.fn(async (filePath: string) =>
    filePath.endsWith("daemon.json") ? '{"host":"127.0.0.1","port":8954}' : "token",
  ),
}));
vi.mock("@tauri-apps/plugin-http", () => ({ fetch: mocks.fetch }));
vi.mock("@tauri-apps/plugin-shell", () => ({ Command: { create: mocks.create } }));

describe("ensureDaemon", () => {
  it("dedupes concurrent starts (StrictMode double mount)", async () => {
    vi.stubEnv("VITE_AGENT_SENTINEL_ROOT", "C:\\root");
    let started = false;
    mocks.fetch.mockImplementation(async () => {
      if (!started) throw new Error("daemon down");
      return { ok: true };
    });
    const spawn = vi.fn(async () => {
      started = true;
    });
    mocks.create.mockReturnValue({ spawn });

    const { ensureDaemon } = await import("./backend");
    await Promise.all([ensureDaemon(), ensureDaemon()]);

    expect(mocks.create).toHaveBeenCalledTimes(1);
    expect(spawn).toHaveBeenCalledTimes(1);
    expect(mocks.create.mock.calls[0]?.[2]).toMatchObject({
      env: { PYTHONUTF8: "1", PYTHONIOENCODING: "utf-8" },
    });
  });

  it("reuses a daemon answering on 8765 when daemon.json is stale", async () => {
    vi.stubEnv("VITE_AGENT_SENTINEL_ROOT", "C:\\root");
    mocks.create.mockClear();
    mocks.fetch.mockImplementation(async (url: string) => {
      if (url.includes(":8765/")) return { ok: true };
      throw new Error("stale daemon.json");
    });

    const { ensureDaemon } = await import("./backend");
    await ensureDaemon();

    expect(mocks.create).not.toHaveBeenCalled();
  });
});
