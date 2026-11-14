// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AgentTable } from "./AgentTable";

afterEach(() => {
  cleanup();
});

describe("AgentTable", () => {
  it("shows an empty invitation when there are no agents", () => {
    render(<AgentTable agents={[]} pendingRole={null} onStop={() => {}} />);

    expect(screen.getByText("还没有 Agent")).toBeInTheDocument();
    expect(screen.getByText("点上方 + 新建角色或本地终端")).toBeInTheDocument();
  });

  it("renders avatar, role and selection; click selects", () => {
    const onSelect = vi.fn();
    render(
      <AgentTable
        agents={[{ role: "dev", pid: 1, alive: true, source: "external" }]}
        pendingRole={null}
        onStop={() => {}}
        onSelect={onSelect}
        selectedRole="dev"
      />,
    );

    expect(screen.getByText("dev")).toBeInTheDocument();
    expect(screen.getByText("DEV")).toBeInTheDocument();
    expect(screen.getByText("外部注册")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /dev/ })).toHaveAttribute("aria-current", "true");

    fireEvent.click(screen.getByText("dev"));
    expect(onSelect).toHaveBeenCalledWith("dev");
  });

  it("shows stop only for managed agents and does not change selection", () => {
    const onSelect = vi.fn();
    const onStop = vi.fn();
    render(
      <AgentTable
        agents={[{ role: "dev", pid: 1, job_id: "job-1", alive: true, source: "local" }]}
        pendingRole={null}
        onStop={onStop}
        onSelect={onSelect}
        selectedRole="dev"
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "停止 dev" }));
    expect(onStop).toHaveBeenCalledWith("dev");
    expect(onSelect).not.toHaveBeenCalled();
  });

  it("offers edit and delete for configured local roles", () => {
    const onEdit = vi.fn();
    const onDelete = vi.fn();
    render(
      <AgentTable
        agents={[
          { role: "reviewer", pid: 42, alive: true, command: "workspace", source: "local" },
        ]}
        pendingRole={null}
        onStop={() => {}}
        onEdit={onEdit}
        onDelete={onDelete}
      />,
    );

    expect(screen.getByText("本地角色")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "编辑 reviewer" }));
    fireEvent.click(screen.getByRole("button", { name: "删除 reviewer" }));

    expect(onEdit).toHaveBeenCalledWith("reviewer");
    expect(onDelete).toHaveBeenCalledWith("reviewer");
    expect(screen.queryByRole("button", { name: /停止/ })).not.toBeInTheDocument();
  });

  it("does not expose actions for external agents", () => {
    render(
      <AgentTable
        agents={[{ role: "doc", pid: 2, alive: true, source: "external" }]}
        pendingRole={null}
        onStop={() => {}}
        onEdit={() => {}}
        onDelete={() => {}}
      />,
    );

    expect(screen.queryByRole("button", { name: /编辑|删除|停止/ })).not.toBeInTheDocument();
  });
});
