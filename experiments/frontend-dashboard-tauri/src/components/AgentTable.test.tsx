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
            job_id: "job-1",
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
    const row = screen.getByText("dev").closest("tr");
    expect(row?.getAttribute("title") ?? row?.querySelector("[title]")?.getAttribute("title")).toContain(
      "T-1",
    );
    fireEvent.click(screen.getByRole("button", { name: "停止" }));
    expect(onStop).toHaveBeenCalledWith("dev");
  });

  it("shows empty state", () => {
    render(<AgentTable agents={[]} pendingRole={null} onStop={() => {}} />);
    expect(screen.getByText("暂无 agent")).toBeInTheDocument();
  });

  it("disables stop while pending", () => {
    render(
      <AgentTable
        agents={[{ role: "dev", job_id: "job-1", alive: true }]}
        pendingRole="dev"
        onStop={() => {}}
      />,
    );
    expect(screen.getByRole("button", { name: "停止中…" })).toBeDisabled();
  });

  it("shows external process instead of stop for unmanaged agents", () => {
    render(
      <AgentTable
        agents={[{ role: "dev", pid: 1234, alive: true }]}
        pendingRole={null}
        onStop={() => {}}
      />,
    );
    expect(screen.getAllByText("外部").length).toBeGreaterThan(0);
    expect(screen.queryByRole("button", { name: "停止" })).not.toBeInTheDocument();
  });

  it("labels configured local roles and hides external marker", () => {
    render(
      <AgentTable
        agents={[{ role: "reviewer", pid: 42, alive: true, command: "workspace" }]}
        pendingRole={null}
        onStop={() => {}}
      />,
    );
    expect(screen.getAllByText("本地").length).toBeGreaterThan(0);
    expect(screen.queryByText("外部")).not.toBeInTheDocument();
  });

  it("selects a role by click and stop does not change selection", () => {
    const onSelect = vi.fn();
    const onStop = vi.fn();
    render(
      <AgentTable
        agents={[{ role: "dev", pid: 1, job_id: "job-1", alive: true }]}
        pendingRole={null}
        onStop={onStop}
        onSelect={onSelect}
        selectedRole="dev"
      />,
    );

    fireEvent.click(screen.getByText("dev"));
    expect(onSelect).toHaveBeenCalledWith("dev");

    fireEvent.click(screen.getByRole("button", { name: "停止" }));
    expect(onStop).toHaveBeenCalledWith("dev");
    expect(onSelect).toHaveBeenCalledTimes(1);
  });
});
