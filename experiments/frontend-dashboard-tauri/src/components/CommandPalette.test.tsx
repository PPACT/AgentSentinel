// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import { CommandPalette } from "./CommandPalette";

beforeAll(() => {
  // cmdk 依赖 ResizeObserver，jsdom 未实现。
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  );
  // cmdk 会调用 scrollIntoView，jsdom 未实现。
  Element.prototype.scrollIntoView = () => {};
});

afterAll(() => {
  vi.unstubAllGlobals();
});

afterEach(() => {
  cleanup();
});

describe("CommandPalette", () => {
  it("renders inside the command context and triggers actions", () => {
    const onRefresh = vi.fn();
    const onReconnect = vi.fn();
    const onClearLogs = vi.fn();
    render(
      <CommandPalette
        open
        onOpenChange={() => {}}
        onRefresh={onRefresh}
        onReconnect={onReconnect}
        onClearLogs={onClearLogs}
      />,
    );

    expect(screen.getByPlaceholderText("输入命令或搜索…")).toBeInTheDocument();
    fireEvent.click(screen.getByText("刷新状态与任务"));
    expect(onRefresh).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByText("重新连接 SSE"));
    expect(onReconnect).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByText("清空 Console 事件"));
    expect(onClearLogs).toHaveBeenCalledTimes(1);
  });
});
