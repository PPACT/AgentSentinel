// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { TaskTable } from "./TaskTable";

afterEach(() => {
  cleanup();
});

describe("TaskTable", () => {
  it("renders task fields", () => {
    render(
      <TaskTable
        tasks={[
          {
            task_id: "T-1",
            from_role: "doc",
            to_role: "dev",
            intent: "implement",
            status: "running",
            updated_at: "2026-11-09T00:00:00Z",
          },
        ]}
      />,
    );
    expect(screen.getByText("T-1")).toBeInTheDocument();
    expect(screen.getByText("doc")).toBeInTheDocument();
    expect(screen.getByText("implement")).toBeInTheDocument();
  });

  it("shows empty state", () => {
    render(<TaskTable tasks={[]} />);
    expect(screen.getByText("暂无任务")).toBeInTheDocument();
  });
});
