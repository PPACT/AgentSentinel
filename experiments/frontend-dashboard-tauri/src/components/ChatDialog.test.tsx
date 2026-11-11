// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ChatDialog } from "./ChatDialog";

const backend = vi.hoisted(() => ({ startChatStream: vi.fn() }));

vi.mock("@/lib/backend", () => ({
  startChatStream: backend.startChatStream,
}));

function streamOf(chunks: string[]): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder();
  return new ReadableStream({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(encoder.encode(chunk));
      controller.close();
    },
  });
}

afterEach(() => {
  cleanup();
  backend.startChatStream.mockReset();
});

describe("ChatDialog", () => {
  it("streams assistant text, thinking and tool blocks", async () => {
    backend.startChatStream.mockResolvedValue({
      body: streamOf([
        'event: message\ndata: {"type":"assistant","message":{"content":[{"type":"thinking","thinking":"想一想"},{"type":"tool_use","name":"Read","input":{"file_path":"a.py"}},{"type":"text","text":"最终回答"}]}}\n\n',
        'event: end\ndata: {"session_id":"session-1","resumed":false,"cost_usd":0.01}\n\n',
      ]),
    });

    render(<ChatDialog open onOpenChange={() => undefined} />);
    fireEvent.change(screen.getByPlaceholderText(/输入内容/), {
      target: { value: "帮我看看 a.py" },
    });
    fireEvent.click(screen.getByRole("button", { name: "发送" }));

    expect(await screen.findByText("最终回答")).toBeInTheDocument();
    expect(screen.getByText("思考过程")).toBeInTheDocument();
    expect(screen.getByText(/Read/)).toBeInTheDocument();
    await waitFor(() =>
      expect(screen.getByText(/session-1/)).toBeInTheDocument(),
    );
    expect(backend.startChatStream).toHaveBeenCalledWith(
      expect.objectContaining({ prompt: "帮我看看 a.py" }),
    );
  });

  it("sends on Enter and keeps Shift+Enter as newline", async () => {
    backend.startChatStream.mockResolvedValue({
      body: streamOf(['event: end\ndata: {"session_id":"s2","resumed":false}\n\n']),
    });

    render(<ChatDialog open onOpenChange={() => undefined} />);
    const input = screen.getByPlaceholderText(/输入内容/);
    fireEvent.change(input, { target: { value: "第一行" } });
    fireEvent.keyDown(input, { key: "Enter", shiftKey: true });
    expect(backend.startChatStream).not.toHaveBeenCalled();

    fireEvent.keyDown(input, { key: "Enter" });
    await waitFor(() => expect(backend.startChatStream).toHaveBeenCalledTimes(1));
  });
});
