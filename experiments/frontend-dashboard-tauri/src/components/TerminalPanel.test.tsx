// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  startPty: vi.fn(),
  sendPtyInput: vi.fn(),
  resizePty: vi.fn(),
  streamFetch: vi.fn(),
  ptyStreamUrl: vi.fn(async () => "http://127.0.0.1/pty/stream"),
}));

vi.mock("@xterm/xterm", () => ({
  Terminal: vi.fn(() => ({
    loadAddon: vi.fn(),
    open: vi.fn(),
    onData: vi.fn(() => ({ dispose: vi.fn() })),
    write: vi.fn(),
    dispose: vi.fn(),
    cols: 80,
    rows: 24,
  })),
}));
vi.mock("@xterm/addon-fit", () => ({ FitAddon: vi.fn(() => ({ fit: vi.fn() })) }));
vi.mock("@/lib/backend", () => ({
  startPty: mocks.startPty,
  sendPtyInput: mocks.sendPtyInput,
  resizePty: mocks.resizePty,
  streamFetch: mocks.streamFetch,
  ptyStreamUrl: mocks.ptyStreamUrl,
}));

import { TerminalPanel } from "./TerminalPanel";

afterEach(() => {
  cleanup();
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
});
