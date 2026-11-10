// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";

import { cleanup, render, screen } from "@testing-library/react";
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import type { Task } from "@/lib/types";

import { TaskDetailsDialog } from "./TaskDetailsDialog";

beforeAll(() => {
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  );
  Element.prototype.scrollIntoView = () => {};
});

afterAll(() => {
  vi.unstubAllGlobals();
});

afterEach(() => {
  cleanup();
});

const task: Task = {
  task_id: "T-1",
  from_role: "doc",
  to_role: "dev",
  intent: "implement",
  pointer: "docs/plan.md",
  status: "running",
  acceptance: ["pytest", "vitest"],
  artifacts: ["src/a.ts"],
  attempts: 1,
  max_tokens: 1000,
  max_minutes: 10,
  cost_tokens: 42,
  created_at: new Date(2026, 10, 9, 9, 5, 3).toISOString(),
  updated_at: new Date(2026, 10, 9, 9, 6, 3).toISOString(),
};

describe("TaskDetailsDialog", () => {
  it("renders fields, quotas and local timestamps", () => {
    render(<TaskDetailsDialog task={task} onOpenChange={() => {}} />);

    expect(screen.getByText("T-1")).toBeInTheDocument();
    expect(screen.getByText("doc → dev")).toBeInTheDocument();
    expect(screen.getByText("implement")).toBeInTheDocument();
    expect(screen.getByText("2026-11-09 09:05:03")).toBeInTheDocument();
    expect(screen.getByText("2026-11-09 09:06:03")).toBeInTheDocument();
    expect(screen.getByText("pytest")).toBeInTheDocument();
    expect(screen.getByText("src/a.ts")).toBeInTheDocument();
    expect(screen.getByText("42")).toBeInTheDocument();
  });

  it("renders nothing when closed", () => {
    render(<TaskDetailsDialog task={null} onOpenChange={() => {}} />);
    expect(screen.queryByText("任务详情")).not.toBeInTheDocument();
  });
});
