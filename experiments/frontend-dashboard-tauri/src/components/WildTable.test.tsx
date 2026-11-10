// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

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
});
