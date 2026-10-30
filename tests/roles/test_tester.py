"""Tester 独立判定与报告契约。"""

import inspect
import json
from pathlib import Path

from agentsentinel.contracts import TestStatus
from agentsentinel.roles import Tester
from agentsentinel.workspace import Workspace


def test_tester_passes_clean_code(tmp_path: Path) -> None:
    workspace = Workspace(tmp_path / "workspace")
    workspace.write_text("code/main.py", "def main() -> str:\n    return 'ok'\n")

    result = Tester().handle("任务", workspace)

    assert result.status is TestStatus.PASS
    assert result.artifacts
    payload = json.loads(result.artifacts[0].read_text(encoding="utf-8"))
    assert payload["status"] == "PASS"
    assert result.artifacts[0].name.startswith("test-")


def test_tester_fails_broken_code(tmp_path: Path) -> None:
    workspace = Workspace(tmp_path / "workspace")
    workspace.write_text("code/main.py", "print('no entry point')\n")

    result = Tester().handle("任务", workspace)

    assert result.status is TestStatus.FAIL
    payload = json.loads(result.artifacts[0].read_text(encoding="utf-8"))
    assert payload["status"] == "FAIL"


def test_tester_has_no_write_or_coder_dependency() -> None:
    assert "file_write" not in Tester.tools
    assert "file_edit" not in Tester.tools
    assert not any(tool.startswith("git") for tool in Tester.tools)

    parameters = inspect.signature(Tester).parameters
    assert list(parameters) == []
