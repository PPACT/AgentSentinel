// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { ChatEntry } from "@/lib/chat";

import { ChatPanel } from "./ChatPanel";

afterEach(() => {
  cleanup();
});

function renderPanel(overrides: Partial<Parameters<typeof ChatPanel>[0]> = {}) {
  const onSend = vi.fn();
  const onStop = vi.fn();
  render(
    <ChatPanel
      role="dev"
      entries={[]}
      busy={false}
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
  it("shows the selected role and a low-noise process", () => {
    renderPanel({ entries: ENTRIES });

    expect(screen.getByText("dev")).toBeInTheDocument();
    expect(screen.getByText("Agent 正在思考…")).toBeInTheDocument();
    expect(screen.getByText("Read")).toBeInTheDocument();
    expect(screen.queryByText("内部推理")).not.toBeInTheDocument();
    expect(screen.getByText("最终回答").tagName).toBe("STRONG");
    expect(document.querySelector("pre code")).not.toBeNull();
  });

  it("disables input and asks for a role when none is selected", () => {
    renderPanel({ role: null });

    const input = screen.getByPlaceholderText("请先在左侧选择角色");
    expect(input).toBeDisabled();
    expect(screen.getByText("请先在左侧选择角色")).toBeInTheDocument();
  });

  it("sends once per Enter and keeps Shift+Enter as newline", () => {
    const { onSend } = renderPanel();
    const input = screen.getByPlaceholderText(/向 dev 发起对话/);
    fireEvent.change(input, { target: { value: "帮我看看" } });

    fireEvent.keyDown(input, { key: "Enter", shiftKey: true });
    expect(onSend).not.toHaveBeenCalled();

    fireEvent.keyDown(input, { key: "Enter" });
    expect(onSend).toHaveBeenCalledWith("帮我看看");
    expect(input).toHaveValue("");
  });

  it("does not send Enter while IME composition is active", () => {
    const { onSend } = renderPanel();
    const input = screen.getByPlaceholderText(/向 dev 发起对话/);
    fireEvent.change(input, { target: { value: "你好" } });

    fireEvent.compositionStart(input);
    fireEvent.keyDown(input, { key: "Enter" });
    expect(onSend).not.toHaveBeenCalled();

    fireEvent.compositionEnd(input);
    fireEvent.keyDown(input, { key: "Enter" });
    expect(onSend).toHaveBeenCalledTimes(1);
  });

  it("shows the inherited-context hint", () => {
    renderPanel({ inheritHint: "已继承 dev 终端上下文，当前为独立会话分支" });
    expect(screen.getByText("已继承 dev 终端上下文，当前为独立会话分支")).toBeInTheDocument();
  });

  it("calls onStop", () => {
    const { onStop } = renderPanel({ busy: true });
    fireEvent.click(screen.getByTitle("停止本轮"));
    expect(onStop).toHaveBeenCalledTimes(1);
  });
});
