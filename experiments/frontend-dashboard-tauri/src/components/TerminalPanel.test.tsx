// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  startPty: vi.fn(),
  sendPtyInput: vi.fn(),
  resizePty: vi.fn(),
  streamFetch: vi.fn(),
  ptyStreamUrl: vi.fn(async () => "http://127.0.0.1/pty/stream"),
  readClipboard: vi.fn(),
  fit: vi.fn(),
  terminals: [] as Array<Record<string, unknown>>,
  webgls: [] as Array<{ onContextLoss: ReturnType<typeof vi.fn>; dispose: ReturnType<typeof vi.fn> }>,
  keyHandlers: [] as Array<(event: KeyboardEvent) => boolean>,
  webglThrows: false,
}));

vi.mock("@xterm/xterm", () => ({
  Terminal: vi.fn(function TerminalMock() {
    const term = {
      loadAddon: vi.fn(),
      open: vi.fn(),
      attachCustomKeyEventHandler: vi.fn((handler: (event: KeyboardEvent) => boolean) => {
        mocks.keyHandlers.push(handler);
      }),
      onData: vi.fn(() => ({ dispose: vi.fn() })),
      paste: vi.fn(),
      focus: vi.fn(),
      write: vi.fn(),
      refresh: vi.fn(),
      dispose: vi.fn(),
      cols: 80,
      rows: 24,
    };
    mocks.terminals.push(term);
    return term;
  }),
}));
vi.mock("@xterm/addon-fit", () => ({
  FitAddon: vi.fn(function FitAddonMock() {
    return { fit: mocks.fit };
  }),
}));
vi.mock("@xterm/addon-webgl", () => ({
  WebglAddon: vi.fn(function WebglAddonMock() {
    if (mocks.webglThrows) {
      mocks.webglThrows = false;
      throw new Error("webgl unavailable");
    }
    const addon = { onContextLoss: vi.fn(), dispose: vi.fn() };
    mocks.webgls.push(addon);
    return addon;
  }),
}));
vi.mock("@/lib/backend", () => ({
  startPty: mocks.startPty,
  sendPtyInput: mocks.sendPtyInput,
  resizePty: mocks.resizePty,
  streamFetch: mocks.streamFetch,
  ptyStreamUrl: mocks.ptyStreamUrl,
}));
vi.mock("@/lib/clipboard", () => ({ readClipboardText: mocks.readClipboard }));

import { TerminalPanel } from "./TerminalPanel";

afterEach(() => {
  cleanup();
});

beforeEach(() => {
  vi.clearAllMocks();
  mocks.terminals.length = 0;
  mocks.webgls.length = 0;
  mocks.keyHandlers.length = 0;
  mocks.webglThrows = false;
  mocks.startPty.mockResolvedValue({ command: "claude", session_id: "session-1" });
  mocks.streamFetch.mockReturnValue(new Promise(() => undefined));
  mocks.readClipboard.mockResolvedValue("pasted text");
});

describe("TerminalPanel", () => {
  it("locks input and shows the offline overlay without starting a PTY", () => {
    render(
      <TerminalPanel
        role="doc"
        locked
        lockReason="当前 Agent 已离线，终端已锁定"
      />,
    );

    expect(screen.getByText("当前 Agent 已离线，终端已锁定")).toBeInTheDocument();
    expect(screen.getByText(/role=doc/)).toBeInTheDocument();
    expect(screen.getByText(/输入已禁用/)).toBeInTheDocument();
    expect(screen.getByText("已锁定")).toBeInTheDocument();
    expect(mocks.startPty).not.toHaveBeenCalled();
  });

  it("keeps the same terminal and stream across 20 tab switches", async () => {
    const { rerender } = render(<TerminalPanel role="dev" agentId="a1" active />);

    await waitFor(() => expect(mocks.startPty).toHaveBeenCalledTimes(1));
    expect(mocks.terminals).toHaveLength(1);

    for (let index = 0; index < 20; index += 1) {
      rerender(<TerminalPanel role="dev" agentId="a1" active={false} />);
      rerender(<TerminalPanel role="dev" agentId="a1" active />);
    }

    await new Promise((resolve) => setTimeout(resolve, 30));
    expect(mocks.terminals).toHaveLength(1);
    expect(mocks.startPty).toHaveBeenCalledTimes(1);
    expect(mocks.fit).toHaveBeenCalled();
  });

  it("falls back when WebGL addon cannot load", async () => {
    mocks.webglThrows = true;
    render(<TerminalPanel role="dev" agentId="a1" active />);

    await waitFor(() => expect(mocks.startPty).toHaveBeenCalledTimes(1));
    expect(mocks.terminals).toHaveLength(1);
    expect(mocks.webgls).toHaveLength(0);
    expect(screen.getByText("Terminal")).toBeInTheDocument();
  });

  it("disposes the WebGL addon and refreshes on context loss", async () => {
    render(<TerminalPanel role="dev" agentId="a1" active />);

    await waitFor(() => expect(mocks.startPty).toHaveBeenCalledTimes(1));
    const addon = mocks.webgls[0];
    expect(addon).toBeDefined();
    const onLoss = addon.onContextLoss.mock.calls[0][0] as () => void;
    onLoss();

    expect(addon.dispose).toHaveBeenCalledTimes(1);
    expect(mocks.terminals[0].refresh).toHaveBeenCalled();
  });

  it("pastes with Ctrl+V through xterm and keeps focus", async () => {
    render(<TerminalPanel role="dev" agentId="a1" active />);
    await waitFor(() => expect(mocks.keyHandlers).toHaveLength(1));
    const handler = mocks.keyHandlers[0];
    const preventDefault = vi.fn();
    const event = {
      type: "keydown",
      key: "v",
      ctrlKey: true,
      metaKey: false,
      altKey: false,
      shiftKey: false,
      preventDefault,
    } as unknown as KeyboardEvent;

    expect(handler(event)).toBe(false);
    expect(preventDefault).toHaveBeenCalled();
    await waitFor(() =>
      expect(mocks.terminals[0].paste).toHaveBeenCalledWith("pasted text"),
    );
    expect(mocks.terminals[0].focus).toHaveBeenCalled();
  });

  it("lets plain Tab and other keys reach the terminal", async () => {
    render(<TerminalPanel role="dev" agentId="a1" active />);
    await waitFor(() => expect(mocks.keyHandlers).toHaveLength(1));
    const handler = mocks.keyHandlers[0];
    const event = {
      type: "keydown",
      key: "Tab",
      ctrlKey: false,
      metaKey: false,
      altKey: false,
      shiftKey: false,
      preventDefault: vi.fn(),
    } as unknown as KeyboardEvent;

    expect(handler(event)).toBe(true);
    expect(mocks.terminals[0].paste).not.toHaveBeenCalled();
  });

  it("offers paste in the right-click context menu", async () => {
    render(<TerminalPanel role="dev" agentId="a1" active />);
    await waitFor(() => expect(mocks.startPty).toHaveBeenCalledTimes(1));

    const section = screen.getByText("Terminal").closest("section");
    expect(section).not.toBeNull();
    fireEvent.contextMenu(section as HTMLElement);
    fireEvent.click(screen.getByRole("menuitem", { name: "粘贴" }));

    await waitFor(() =>
      expect(mocks.terminals[0].paste).toHaveBeenCalledWith("pasted text"),
    );
  });
});
