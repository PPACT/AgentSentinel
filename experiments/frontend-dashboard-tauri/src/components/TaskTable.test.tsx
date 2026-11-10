// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

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
    expect(screen.getByText("doc → dev")).toBeInTheDocument();
    expect(screen.getByText("implement")).toBeInTheDocument();
  });

  it("shows empty state", () => {
    render(<TaskTable tasks={[]} />);
    expect(screen.getByText("暂无任务")).toBeInTheDocument();
  });

  it("selects a task on click and Enter", () => {
    const onSelect = vi.fn();
    const task = {
      task_id: "T-2",
      from_role: "doc",
      to_role: "dev",
      intent: "review",
      status: "pending",
      updated_at: "2026-11-09T00:00:00Z",
    };
    render(<TaskTable tasks={[task]} onSelect={onSelect} />);

    fireEvent.click(screen.getByText("T-2"));
    expect(onSelect).toHaveBeenCalledWith(task);

    const row = screen.getByText("T-2").closest("tr");
    expect(row).not.toBeNull();
    fireEvent.keyDown(row!, { key: "Enter" });
    expect(onSelect).toHaveBeenCalledTimes(2);
  });
});
