// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AgentTable } from "./AgentTable";

afterEach(() => {
  cleanup();
});

describe("AgentTable", () => {
  it("renders agent fields and fires stop", () => {
    const onStop = vi.fn();
    render(
      <AgentTable
        agents={[
          {
            role: "dev",
            pid: 1234,
            pgid: 1234,
            status: "registered",
            alive: true,
            last_heartbeat: "2026-11-09T00:00:00Z",
            current_task_id: "T-1",
          },
        ]}
        pendingRole={null}
        onStop={onStop}
      />,
    );
    expect(screen.getByText("dev")).toBeInTheDocument();
    expect(screen.getByText("在线")).toBeInTheDocument();
    expect(screen.getByText("T-1")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "停止" }));
    expect(onStop).toHaveBeenCalledWith("dev");
  });

  it("shows empty state", () => {
    render(<AgentTable agents={[]} pendingRole={null} onStop={() => {}} />);
    expect(screen.getByText("暂无 agent")).toBeInTheDocument();
  });

  it("disables stop while pending", () => {
    render(<AgentTable agents={[{ role: "dev" }]} pendingRole="dev" onStop={() => {}} />);
    expect(screen.getByRole("button", { name: "停止中…" })).toBeDisabled();
  });
});
