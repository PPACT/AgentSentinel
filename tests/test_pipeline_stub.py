"""S1 离线编排：正常链路、DENY 阻断、审计字段与无网络证明。"""

import json
import socket
from pathlib import Path

import pytest

from agentsentinel.__main__ import main
from agentsentinel.governance import DenyAllSanitizer
from agentsentinel.pipeline import StubPipeline
from agentsentinel.roles import PipelineOutcome
from agentsentinel.tools import StubGitOps
from agentsentinel.workspace import Workspace

AUDIT_FIELDS = {
    "timestamp",
    "caller",
    "commit_hash",
    "scan_result",
    "test_result",
    "action_type",
    "reason",
}


def _read_audit(path: Path) -> list[dict[str, object]]:
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


def test_normal_run_writes_artifacts_and_audit(tmp_path: Path) -> None:
    workspace = Workspace(tmp_path / "workspace")

    result = StubPipeline(workspace).run("离线演示任务")

    assert result.status is PipelineOutcome.PASSED
    assert (workspace.code_dir / "main.py").is_file()
    assert (workspace.docs_dir / "CHANGELOG.md").is_file()
    assert list(workspace.reports_dir.glob("test-*.json"))

    records = _read_audit(result.audit_log)
    assert len(records) == 1
    record = records[0]
    assert set(record) == AUDIT_FIELDS
    assert record["action_type"] == "push"
    assert record["scan_result"] == "ALLOW"
    assert record["test_result"] == "PASS"
    assert record["caller"] == "Orchestrator"
    assert str(record["commit_hash"]).startswith("stub")
    assert record["reason"] == ""


def test_deny_blocks_before_any_git_call_and_is_audited(tmp_path: Path) -> None:
    workspace = Workspace(tmp_path / "workspace")
    git_ops = StubGitOps()

    result = StubPipeline(
        workspace,
        sanitizer=DenyAllSanitizer(),
        git_ops=git_ops,
    ).run("离线演示任务")

    assert result.status is PipelineOutcome.DENIED
    assert git_ops.calls == []
    records = _read_audit(result.audit_log)
    assert len(records) == 1
    assert records[0]["scan_result"] == "DENY"
    assert records[0]["action_type"] == "push"
    assert records[0]["commit_hash"] == ""
    assert str(records[0]["reason"]).startswith("SCAN_DENIED:")


def test_run_makes_no_network_calls(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def _no_network(*args: object, **kwargs: object) -> None:
        raise AssertionError("network access attempted")

    monkeypatch.setattr(socket, "socket", _no_network)

    result = StubPipeline(Workspace(tmp_path / "workspace")).run("离线演示任务")

    assert result.status is PipelineOutcome.PASSED


def test_cli_stub_runs_offline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)

    assert main(["--stub"]) == 0
    assert (tmp_path / "workspace" / "code" / "main.py").is_file()
    assert (tmp_path / "workspace" / "audit" / "push-log.jsonl").is_file()


def test_cli_requires_stub_flag() -> None:
    assert main([]) == 2
