"""guard_subagents（PreToolUse）：Task / Agent 一律 DENY，其他工具静默。"""

from __future__ import annotations

from typing import Any

import pytest

from tests.hooks.hook_utils import hook_output, run_hook


def _payload(tool: str) -> dict[str, Any]:
    return {
        "hook_event_name": "PreToolUse",
        "tool_name": tool,
        "tool_input": {},
        "cwd": ".",
    }


@pytest.mark.parametrize("tool", ["Task", "Agent"])
def test_task_and_agent_are_denied(tool: str) -> None:
    output = hook_output(run_hook("guard_subagents.py", _payload(tool)))

    assert output["permissionDecision"] == "deny"
    assert "SendMessage" in output["permissionDecisionReason"]


@pytest.mark.parametrize("tool", ["Bash", "Edit", "Write", "Read"])
def test_other_tools_are_silent(tool: str) -> None:
    result = run_hook("guard_subagents.py", _payload(tool))

    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
