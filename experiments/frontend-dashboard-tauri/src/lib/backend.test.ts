import { describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  fetch: vi.fn(),
  create: vi.fn(),
}));

vi.mock("@tauri-apps/api/path", () => ({
  appDataDir: vi.fn(async () => "C:\\app"),
  join: vi.fn(async (...parts: string[]) => parts.join("\\")),
}));
vi.mock("@tauri-apps/plugin-fs", () => ({ readTextFile: vi.fn(async () => "token") }));
vi.mock("@tauri-apps/plugin-http", () => ({ fetch: mocks.fetch }));
vi.mock("@tauri-apps/plugin-shell", () => ({ Command: { create: mocks.create } }));

describe("ensureDaemon", () => {
  it("dedupes concurrent starts (StrictMode double mount)", async () => {
    vi.stubEnv("VITE_AGENT_SENTINEL_ROOT", "C:\\root");
    let healthChecks = 0;
    mocks.fetch.mockImplementation(async () => {
      healthChecks += 1;
      if (healthChecks === 1) throw new Error("daemon down");
      return { ok: true };
    });
    const spawn = vi.fn(async () => undefined);
    mocks.create.mockReturnValue({ spawn });

    const { ensureDaemon } = await import("./backend");
    await Promise.all([ensureDaemon(), ensureDaemon()]);

    expect(mocks.create).toHaveBeenCalledTimes(1);
    expect(spawn).toHaveBeenCalledTimes(1);
  });
});
