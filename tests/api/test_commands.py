"""P1-4 Fast Path 生产接线：固定命令直接调用角色 handle。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentsentinel.api.commands import (
    SYNC_DOCS_TASK,
    default_command_handlers,
    dispatch,
)
from agentsentinel.workspace import Workspace


def _workspace(tmp_path: Path) -> Workspace:
    workspace = Workspace(tmp_path / "workspace")
    workspace.ensure()
    return workspace


def _write_main(workspace: Workspace) -> None:
    workspace.write_text("code/main.py", "def main() -> None:\n    pass\n")


def test_default_handlers_expose_only_fixed_commands(tmp_path: Path) -> None:
    handlers = default_command_handlers(_workspace(tmp_path))

    assert set(handlers) == {"run-test", "sync-docs"}


def test_run_test_writes_report_without_model(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    _write_main(workspace)

    dispatch(default_command_handlers(workspace), "run-test", "冒烟")

    reports = list(workspace.reports_dir.glob("test-*.json"))
    assert len(reports) == 1
    payload = json.loads(reports[0].read_text(encoding="utf-8"))
    assert payload["status"] == "PASS"


def test_sync_docs_writes_changelog(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)

    dispatch(default_command_handlers(workspace), "sync-docs", "更新说明")

    changelog = workspace.docs_dir / "CHANGELOG.md"
    assert changelog.is_file()
    assert "更新说明" in changelog.read_text(encoding="utf-8")


def test_empty_arguments_use_fast_path_fallback(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)

    dispatch(default_command_handlers(workspace), "sync-docs", "")

    changelog = (workspace.docs_dir / "CHANGELOG.md").read_text(encoding="utf-8")
    assert SYNC_DOCS_TASK in changelog


def test_dispatch_rejects_unknown_command(tmp_path: Path) -> None:
    handlers = default_command_handlers(_workspace(tmp_path))

    with pytest.raises(ValueError):
        dispatch(handlers, "deploy", "")
