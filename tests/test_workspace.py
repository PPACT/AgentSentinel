"""Workspace 目录、文本读写与测试报告路径测试。"""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from agentsentinel.contracts import TestReport, TestStatus
from agentsentinel.workspace import Workspace


def test_ensure_creates_artifact_directories(tmp_path: Path) -> None:
    workspace = Workspace(tmp_path / "workspace")

    workspace.ensure()

    assert workspace.code_dir.is_dir()
    assert workspace.docs_dir.is_dir()
    assert workspace.reports_dir.is_dir()
    assert workspace.audit_dir.is_dir()


def test_ensure_is_idempotent(tmp_path: Path) -> None:
    workspace = Workspace(tmp_path / "workspace")
    workspace.ensure()
    marker = workspace.code_dir / "keep.txt"
    marker.write_text("keep", encoding="utf-8")

    workspace.ensure()

    assert marker.read_text(encoding="utf-8") == "keep"


def test_write_and_read_text(tmp_path: Path) -> None:
    workspace = Workspace(tmp_path / "workspace")

    written = workspace.write_text("code/main.py", "print('hi')\n")

    assert written == workspace.root / "code" / "main.py"
    assert workspace.read_text("code/main.py") == "print('hi')\n"


def test_write_text_rejects_path_traversal(tmp_path: Path) -> None:
    workspace = Workspace(tmp_path / "workspace")

    with pytest.raises(ValueError):
        workspace.write_text("../outside.txt", "nope")


def test_report_path_uses_spec_format(tmp_path: Path) -> None:
    workspace = Workspace(tmp_path / "workspace")
    timestamp = datetime(2026, 10, 30, 12, 0, tzinfo=UTC)

    path = workspace.report_path(timestamp)

    assert path == workspace.root / "reports" / "test-20261030T120000Z.json"


def test_write_report_persists_strict_json(tmp_path: Path) -> None:
    workspace = Workspace(tmp_path / "workspace")
    report = TestReport(
        status=TestStatus.FAIL,
        summary="1 failed",
        details="assert 1 == 2",
        timestamp=datetime(2026, 10, 30, 12, 0, tzinfo=UTC),
    )

    path = workspace.write_report(report)

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["status"] == "FAIL"
    assert payload["summary"] == "1 failed"
    assert path.name.startswith("test-")
