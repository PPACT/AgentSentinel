import { describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  fetch: vi.fn(),
  invoke: vi.fn(),
  stopping: { value: false },
}));

vi.mock("@tauri-apps/api/path", () => ({
  join: vi.fn(async (...parts: string[]) => parts.join("\\")),
}));
vi.mock("@tauri-apps/plugin-fs", () => ({
  readTextFile: vi.fn(async (filePath: string) => {
    if (filePath.endsWith("daemon.json")) {
      if (mocks.stopping.value) throw new Error("daemon.json removed");
      return '{"host":"127.0.0.1","port":8954,"pid":4242}';
    }
    return "token";
  }),
}));
vi.mock("@tauri-apps/plugin-http", () => ({ fetch: mocks.fetch }));
vi.mock("@tauri-apps/api/core", () => ({ invoke: mocks.invoke }));

describe("ensureDaemon", () => {
  it("dedupes concurrent starts (StrictMode double mount)", async () => {
    vi.stubEnv("VITE_AGENT_SENTINEL_ROOT", "C:\\root");
    mocks.stopping.value = false;
    let started = false;
    mocks.fetch.mockImplementation(async () => {
      if (!started) throw new Error("daemon down");
      return { ok: true };
    });
    mocks.invoke.mockImplementation(async () => {
      started = true;
    });

    const { ensureDaemon } = await import("./backend");
    await Promise.all([ensureDaemon(), ensureDaemon()]);

    expect(mocks.invoke).toHaveBeenCalledTimes(1);
    expect(mocks.invoke).toHaveBeenCalledWith("start_daemon", { root: "C:\\root" });
  });

  it("reuses a healthy control daemon without starting a new one", async () => {
    vi.stubEnv("VITE_AGENT_SENTINEL_ROOT", "C:\\root");
    mocks.stopping.value = false;
    mocks.invoke.mockClear();
    mocks.fetch.mockImplementation(async () => ({ ok: true }));

    const { ensureDaemon } = await import("./backend");
    await ensureDaemon();

    expect(mocks.invoke).not.toHaveBeenCalled();
  });
});

describe("shutdownDaemon", () => {
  it("posts /shutdown and waits until health stops answering", async () => {
    vi.stubEnv("VITE_AGENT_SENTINEL_ROOT", "C:\\root");
    mocks.stopping.value = false;
    mocks.fetch.mockImplementation(async (url: string) => {
      if (url.endsWith("/shutdown")) {
        mocks.stopping.value = true;
        return { ok: true };
      }
      if (mocks.stopping.value) throw new Error("daemon exited");
      return { ok: true };
    });

    const { shutdownDaemon } = await import("./backend");
    const result = await shutdownDaemon(2_000);

    expect(result).toEqual({ exited: true, pid: 4242 });
    expect(mocks.fetch).toHaveBeenCalledWith(
      "http://127.0.0.1:8954/shutdown",
      expect.objectContaining({ method: "POST" }),
    );
  });
});

describe("pty agent binding", () => {
  it("carries role and agent_id in start/input/history calls", async () => {
    vi.stubEnv("VITE_AGENT_SENTINEL_ROOT", "C:\\root");
    mocks.stopping.value = false;
    mocks.fetch.mockResolvedValue({ ok: true, text: async () => "{}" });

    const { getPtyHistory, sendPtyInput, startPty } = await import("./backend");
    await startPty("doc", "id-doc");
    expect(mocks.fetch).toHaveBeenCalledWith(
      "http://127.0.0.1:8954/pty/1/start",
      expect.objectContaining({ body: JSON.stringify({ role: "doc", agent_id: "id-doc" }) }),
    );

    await sendPtyInput("hi", "doc", "id-doc");
    expect(mocks.fetch).toHaveBeenCalledWith(
      "http://127.0.0.1:8954/pty/1/input",
      expect.objectContaining({
        body: JSON.stringify({ data: "hi", role: "doc", agent_id: "id-doc" }),
      }),
    );

    await getPtyHistory("doc", "id-doc");
    expect(mocks.fetch).toHaveBeenCalledWith(
      "http://127.0.0.1:8954/pty/1/history?role=doc&agent_id=id-doc",
      expect.anything(),
    );
  });
});
