// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  save: vi.fn(),
  preflight: vi.fn(),
  activate: vi.fn(),
  remove: vi.fn(),
}));

vi.mock("@/lib/backend", () => ({
  saveWorkspace: mocks.save,
  preflightWorkspace: mocks.preflight,
  activateWorkspace: mocks.activate,
  deleteWorkspace: mocks.remove,
}));

import type { EngineOption, Workspace, WorkspacesResponse } from "@/lib/types";

import { WorkspaceDialog } from "./WorkspaceDialog";

const WORKSPACE: Workspace = {
  id: 1,
  name: "AgentSentinel",
  project_path: "D:\\Code\\AgentSentinel",
  command: "claude",
  engine: "claude",
  engine_label: "Claude Code",
  wake_prompt: "",
  wake_prompt_file: "",
  budget_tokens: 200_000,
  active: 1,
  capabilities: { chat: true, usage: true, clear: true },
};

const ENGINES: EngineOption[] = [
  { id: "claude", label: "Claude Code", default_command: "claude", chat: true, usage: true, clear: true },
  { id: "codex", label: "Codex CLI", default_command: "codex", chat: false, usage: false, clear: false },
  { id: "generic", label: "通用 CLI", default_command: "", chat: false, usage: false, clear: false },
];

function renderDialog() {
  const onRefresh = vi.fn();
  render(
    <WorkspaceDialog
      open
      onOpenChange={() => undefined}
      workspaces={[WORKSPACE]}
      engines={ENGINES}
      activeId={1}
      onRefresh={onRefresh}
    />,
  );
  return { onRefresh };
}

beforeEach(() => {
  mocks.save.mockReset();
  mocks.preflight.mockReset();
  mocks.activate.mockReset();
  mocks.remove.mockReset();
});

afterEach(() => {
  cleanup();
});

describe("WorkspaceDialog", () => {
  it("switches the preset engine and fills its default command", () => {
    renderDialog();

    fireEvent.change(screen.getByLabelText("引擎预设"), { target: { value: "codex" } });

    expect(screen.getByLabelText(/启动命令/)).toHaveValue("codex");
  });

  it("shows the preflight error without starting anything", async () => {
    mocks.preflight.mockResolvedValue({
      ok: false,
      reason: "command_not_found",
      error: "未检测到 codex 命令，请先配置好环境",
    });
    renderDialog();

    fireEvent.click(screen.getByRole("button", { name: "检测环境" }));

    await waitFor(() =>
      expect(screen.getByText("未检测到 codex 命令，请先配置好环境")).toBeInTheDocument(),
    );
  });

  it("saves the configured command and engine", async () => {
    const saved: WorkspacesResponse = {
      workspaces: [
        {
          ...WORKSPACE,
          engine: "codex",
          command: "codex",
          capabilities: { chat: false, usage: false, clear: false },
        },
      ],
      active_id: 1,
      engines: ENGINES,
      workspace: { ...WORKSPACE, engine: "codex", command: "codex" },
    };
    mocks.save.mockResolvedValue(saved);
    const { onRefresh } = renderDialog();

    fireEvent.change(screen.getByLabelText("引擎预设"), { target: { value: "codex" } });
    fireEvent.click(screen.getByRole("button", { name: "保存" }));

    await waitFor(() => expect(mocks.save).toHaveBeenCalledTimes(1));
    expect(mocks.save.mock.calls[0][0]).toMatchObject({ engine: "codex", command: "codex" });
    expect(onRefresh).toHaveBeenCalledWith(saved);
  });
});
