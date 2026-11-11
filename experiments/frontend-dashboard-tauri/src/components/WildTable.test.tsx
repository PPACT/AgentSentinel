// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { WildTable } from "./WildTable";

afterEach(() => {
  cleanup();
});

describe("WildTable", () => {
  it("renders wild process roots with child count", () => {
    render(
      <WildTable
        processes={[
          {
            pid: 4321,
            cwd: "D:\\proj",
            cmdline: "python agent.py",
            child_pids: [4322, 4323],
          },
        ]}
      />,
    );

    expect(screen.getByText("4321")).toBeInTheDocument();
    expect(screen.getByText("python agent.py")).toBeInTheDocument();
    expect(screen.getByText("2")).toBeInTheDocument();
  });

  it("shows empty state", () => {
    render(<WildTable processes={[]} />);

    expect(screen.getByText("未发现未注册进程")).toBeInTheDocument();
  });

  it("offers force cleanup for node/python residue", () => {
    const onCleanup = vi.fn();
    render(
      <WildTable
        processes={[
          {
            pid: 4321,
            cwd: "D:\\proj",
            cmdline: "node.exe server.js",
            create_time: 100,
          },
        ]}
        onCleanup={onCleanup}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "强制清理" }));

    expect(onCleanup).toHaveBeenCalledWith(expect.objectContaining({ pid: 4321 }));
  });

  it("keeps non node/python processes read-only", () => {
    render(
      <WildTable
        processes={[
          {
            pid: 4321,
            cwd: "D:\\proj",
            cmdline: "claude.exe -p task",
            create_time: 100,
          },
        ]}
        onCleanup={() => undefined}
      />,
    );

    expect(screen.queryByRole("button", { name: "强制清理" })).not.toBeInTheDocument();
    expect(screen.getByText("只读")).toBeInTheDocument();
  });
});
