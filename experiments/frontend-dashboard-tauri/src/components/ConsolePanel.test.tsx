// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { makeLogEntry } from "@/lib/log";

import { ConsolePanel } from "./ConsolePanel";

vi.mock("@tanstack/react-virtual", () => ({
  useVirtualizer: ({ count }: { count: number }) => ({
    getTotalSize: () => count * 20,
    getVirtualItems: () =>
      Array.from({ length: count }, (_, index) => ({ index, size: 20, start: index * 20 })),
  }),
}));

const logs = [
  makeLogEntry(1, "info", "[dev] started", new Date(), "dev"),
  makeLogEntry(2, "warn", "[doc] slow", new Date(), "doc"),
  makeLogEntry(3, "info", "SSE 已连接"),
];

afterEach(() => {
  cleanup();
});

describe("ConsolePanel", () => {
  it("filters by role and query", () => {
    render(<ConsolePanel logs={logs} />);
    expect(screen.getByText("[dev] started")).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("按角色过滤"), { target: { value: "doc" } });
    expect(screen.queryByText("[dev] started")).not.toBeInTheDocument();
    expect(screen.getByText("[doc] slow")).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("按角色过滤"), { target: { value: "" } });
    fireEvent.change(screen.getByLabelText("搜索日志"), { target: { value: "sse" } });
    expect(screen.getByText("SSE 已连接")).toBeInTheDocument();
    expect(screen.queryByText("[dev] started")).not.toBeInTheDocument();
  });

  it("shows no-match state and counts filtered entries", () => {
    render(<ConsolePanel logs={logs} />);
    fireEvent.change(screen.getByLabelText("搜索日志"), { target: { value: "没有这条" } });
    expect(screen.getByText("无匹配日志")).toBeInTheDocument();
    expect(screen.getByText("0/3")).toBeInTheDocument();
  });

  it("toggles autoscroll and fires clear", () => {
    const onClear = vi.fn();
    render(<ConsolePanel logs={logs} onClear={onClear} />);

    const toggle = screen.getByRole("button", { name: "切换自动滚动" });
    expect(toggle).toHaveTextContent("自动滚动");
    fireEvent.click(toggle);
    expect(toggle).toHaveTextContent("已暂停");
    fireEvent.click(toggle);
    expect(toggle).toHaveTextContent("自动滚动");

    fireEvent.click(screen.getByRole("button", { name: "清空" }));
    expect(onClear).toHaveBeenCalledTimes(1);
  });

  it("shows the empty state", () => {
    render(<ConsolePanel logs={[]} />);
    expect(screen.getByText("暂无事件")).toBeInTheDocument();
  });
});
