// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { ChatEntry } from "@/lib/chat";

import { ChatPanel } from "./ChatPanel";

if (typeof globalThis.ResizeObserver === "undefined") {
  globalThis.ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  } as unknown as typeof ResizeObserver;
}

afterEach(() => {
  cleanup();
});

function renderPanel(overrides: Partial<Parameters<typeof ChatPanel>[0]> = {}) {
  const onSend = vi.fn();
  const onStop = vi.fn();
  render(
    <ChatPanel
      entries={[]}
      busy={false}
      sessionId="session-12345678"
      onSend={onSend}
      onStop={onStop}
      {...overrides}
    />,
  );
  return { onSend, onStop };
}

const ENTRIES: ChatEntry[] = [
  { id: 1, block: { kind: "thinking", text: "内部推理" } },
  { id: 2, block: { kind: "tool", name: "Read", input: "读取 a.py" } },
  { id: 3, block: { kind: "text", text: "**最终回答**\n\n```ts\nconst x = 1;\n```" } },
];

describe("ChatPanel", () => {
  it("shows the PTY session and a low-noise process", () => {
    renderPanel({ entries: ENTRIES });

    expect(screen.getByText("PTY")).toBeInTheDocument();
    expect(screen.getByText(/session session/)).toBeInTheDocument();
    expect(screen.queryByText("内部推理")).not.toBeInTheDocument();
    expect(screen.queryByText(/Agent 正在思考/)).not.toBeInTheDocument();
    expect(screen.getByText("执行了 1 个工具调用")).toBeInTheDocument();
    fireEvent.click(screen.getByText("执行了 1 个工具调用"));
    expect(screen.getByText("Read")).toBeInTheDocument();
    expect(screen.getByText("最终回答").tagName).toBe("STRONG");
    expect(document.querySelector("pre code")).not.toBeNull();
  });

  it("groups bash calls into one expandable status row", () => {
    renderPanel({
      entries: [
        { id: 1, block: { kind: "tool", name: "Bash", input: "npm test" } },
        { id: 2, block: { kind: "thinking", text: "中间思考" } },
        { id: 3, block: { kind: "tool", name: "Bash", input: "ruff check ." } },
        { id: 4, block: { kind: "tool", name: "Bash", input: "git status" } },
      ],
    });

    expect(screen.getByText("执行了 3 条系统命令")).toBeInTheDocument();
    fireEvent.click(screen.getByText("执行了 3 条系统命令"));
    expect(screen.getByText("npm test")).toBeInTheDocument();
    expect(screen.getByText("ruff check .")).toBeInTheDocument();
  });

  it("keeps only the final text of a turn", () => {
    renderPanel({
      entries: [
        { id: 1, block: { kind: "user", text: "问题" } },
        { id: 2, block: { kind: "text", text: "中间回答" } },
        { id: 3, block: { kind: "tool", name: "Bash", input: "npm test" } },
        { id: 4, block: { kind: "text", text: "**最终回答**" } },
      ],
    });

    expect(screen.getByText("问题")).toBeInTheDocument();
    expect(screen.queryByText("中间回答")).not.toBeInTheDocument();
    expect(screen.getByText("最终回答").tagName).toBe("STRONG");
  });

  it("shows a silent thinking pulse while busy", () => {
    renderPanel({ busy: true });
    expect(screen.getByText("思考中")).toBeInTheDocument();
  });

  it("sends on Enter and keeps Shift+Enter as newline", () => {
    const { onSend } = renderPanel();
    const input = screen.getByPlaceholderText("向 PTY 发送…");
    fireEvent.change(input, { target: { value: "帮我看看" } });

    fireEvent.keyDown(input, { key: "Enter", shiftKey: true });
    expect(onSend).not.toHaveBeenCalled();

    fireEvent.keyDown(input, { key: "Enter" });
    expect(onSend).toHaveBeenCalledWith("帮我看看");
    expect(input).toHaveValue("");
  });

  it("does not send Enter while IME composition is active", () => {
    const { onSend } = renderPanel();
    const input = screen.getByPlaceholderText("向 PTY 发送…");
    fireEvent.change(input, { target: { value: "你好" } });

    fireEvent.compositionStart(input);
    fireEvent.keyDown(input, { key: "Enter" });
    expect(onSend).not.toHaveBeenCalled();

    fireEvent.compositionEnd(input);
    fireEvent.keyDown(input, { key: "Enter" });
    expect(onSend).toHaveBeenCalledTimes(1);
  });

  it("shows the context warning in a tooltip on hover", async () => {
    renderPanel({ contextWarning: "PTY 上下文约 195k tokens；继续对话按输入计费" });
    const dot = screen.getByLabelText("上下文用量");
    fireEvent.mouseEnter(dot);
    fireEvent.focus(dot);
    await waitFor(() =>
      expect(screen.getByText(/195k tokens/)).toBeInTheDocument(),
    );
  });

  it("calls onStop", () => {
    const { onStop } = renderPanel({ busy: true });
    fireEvent.click(screen.getByTitle("停止本轮"));
    expect(onStop).toHaveBeenCalledTimes(1);
  });

  it("locks the composer when the selected agent is offline", () => {
    const { onSend } = renderPanel({ locked: true });
    const input = screen.getByPlaceholderText("Agent 已离线，输入已禁用");

    expect(screen.getByText("当前 Agent 已离线，对话已锁定")).toBeInTheDocument();
    expect(input).toBeDisabled();
    fireEvent.keyDown(input, { key: "Enter" });
    expect(onSend).not.toHaveBeenCalled();
  });

  it("shows session token usage and budget progress", () => {
    renderPanel({
      usage: {
        input_tokens: 1_234,
        output_tokens: 567,
        cache_read_input_tokens: 10,
        cache_creation_input_tokens: 20,
      },
      contextTokens: 5_000,
      budgetTokens: 200_000,
    });

    expect(screen.getByLabelText("Token 用量")).toBeInTheDocument();
    expect(screen.getByText(/in 1k · out 567/)).toBeInTheDocument();
  });

  it("shows PTY notices such as session conflict", () => {
    renderPanel({
      notice: {
        seq: 1,
        kind: "session_conflict",
        message: "旧会话正被外部占用，已为您开启全新干净会话",
      },
    });

    expect(screen.getByText(/旧会话正被外部占用/)).toBeInTheDocument();
  });
});
