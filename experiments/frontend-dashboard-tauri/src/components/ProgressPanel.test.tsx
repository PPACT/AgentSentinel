// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import type { ChatEntry } from "@/lib/chat";

import { ProgressPanel } from "./ProgressPanel";

afterEach(() => {
  cleanup();
});

const ENTRIES: ChatEntry[] = [
  {
    id: 1,
    timestamp: "2026-11-12T08:00:00Z",
    block: { kind: "tool", name: "Bash", input: "npm test" },
  },
  { id: 2, block: { kind: "tool", name: "Edit", input: "src/App.tsx" } },
  { id: 3, block: { kind: "tool", name: "Edit", input: "src/App.tsx" } },
  { id: 4, block: { kind: "tool", name: "Read", input: "README.md" } },
  { id: 5, block: { kind: "text", text: "完成" } },
];

describe("ProgressPanel", () => {
  it("summarizes commands, files and reads in a timeline", () => {
    render(<ProgressPanel role="dev" entries={ENTRIES} />);

    expect(screen.getByText("工具 4")).toBeInTheDocument();
    expect(screen.getByText("命令 1")).toBeInTheDocument();
    expect(screen.getByText("文件 1")).toBeInTheDocument();
    expect(screen.getByText("读取 1")).toBeInTheDocument();
    expect(screen.getByText("npm test")).toBeInTheDocument();
    expect(screen.getAllByText("src/App.tsx")).toHaveLength(2);
    expect(screen.getByText("README.md")).toBeInTheDocument();
    expect(screen.queryByText("完成")).not.toBeInTheDocument();
  });

  it("asks for a role and shows an empty state", () => {
    const { rerender } = render(<ProgressPanel role={null} entries={[]} />);
    expect(screen.getByText("请先在左侧选择角色")).toBeInTheDocument();

    rerender(<ProgressPanel role="doc" entries={[]} />);
    expect(screen.getByText("暂无进度记录")).toBeInTheDocument();
  });
});
