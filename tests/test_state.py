"""P2-1 状态流地基：StateRecord 契约、JSONL 写入器与编排流转落点。

状态流转与审计解耦：流转写 ``workspace/reports/pipeline-state.jsonl``，
``push-log.jsonl`` 只保留 push / revert / DENY。
"""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from agentsentinel.contracts import StateRecord, TestStatus
from agentsentinel.governance import DenyAllSanitizer
from agentsentinel.pipeline import SentinelPipeline
from agentsentinel.roles import AgentResult, PipelineOutcome, Tester
from agentsentinel.state import StateLogger
from agentsentinel.tools import StubGitOps
from agentsentinel.workspace import Workspace


class _AlwaysFailingTester(Tester):
    def handle(self, task: str, workspace: Workspace, *, feedback: str = "") -> AgentResult:
        return AgentResult(agent=self.name, summary="forced failure", status=TestStatus.FAIL)


def _read_lines(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _transitions(path: Path) -> list[tuple[str, str]]:
    return [(str(record["from_state"]), str(record["to_state"])) for record in _read_lines(path)]


def _record(**overrides: str) -> StateRecord:
    fields: dict[str, object] = {
        "timestamp": datetime.now(UTC),
        "from_state": "coding",
        "to_state": "coded",
        "detail": "",
    }
    fields.update(overrides)
    return StateRecord.model_validate(fields)


# --- 契约 -----------------------------------------------------------------


def test_state_record_fields_and_defaults() -> None:
    record = _record(from_state="coding", to_state="coded", detail="编码完成")

    assert record.from_state == "coding"
    assert record.to_state == "coded"
    assert record.detail == "编码完成"
    assert StateRecord.model_fields["detail"].default == ""


def test_state_record_forbids_extra_fields() -> None:
    with pytest.raises(ValidationError):
        StateRecord(
            timestamp=datetime.now(UTC),
            from_state="coding",
            to_state="coded",
            reason="x",  # type: ignore[call-arg]
        )


# --- 写入器 ----------------------------------------------------------------


def test_state_logger_appends_jsonl_and_creates_parent(tmp_path: Path) -> None:
    log_path = tmp_path / "nested" / "reports" / "pipeline-state.jsonl"
    logger = StateLogger(log_path)

    logger.record(_record(to_state="coded"))
    logger.record(_record(from_state="coded", to_state="test_passed"))

    records = _read_lines(log_path)
    assert len(records) == 2
    assert records[0]["to_state"] == "coded"
    assert records[1]["from_state"] == "coded"
    assert records[1]["to_state"] == "test_passed"
    assert logger.log_path == log_path


def test_state_logger_propagates_write_failure(tmp_path: Path) -> None:
    occupied = tmp_path / "pipeline-state.jsonl"
    occupied.mkdir()

    with pytest.raises(OSError):
        StateLogger(occupied).record(_record())


# --- 编排流转落点 ----------------------------------------------------------


def test_publish_path_records_four_transitions(tmp_path: Path) -> None:
    workspace = Workspace(tmp_path / "workspace")

    result = SentinelPipeline(workspace, git_ops=StubGitOps()).run("实现登录接口")

    assert result.status is PipelineOutcome.PASSED
    assert _transitions(workspace.pipeline_state_path) == [
        ("coding", "coded"),
        ("coded", "test_passed"),
        ("test_passed", "doc_ready"),
        ("doc_ready", "published"),
    ]


def test_denied_path_records_terminal_denied(tmp_path: Path) -> None:
    workspace = Workspace(tmp_path / "workspace")

    result = SentinelPipeline(
        workspace,
        sanitizer=DenyAllSanitizer(),
        git_ops=StubGitOps(),
    ).run("实现登录接口")

    assert result.status is PipelineOutcome.DENIED
    transitions = _transitions(workspace.pipeline_state_path)
    assert transitions[-1] == ("doc_ready", "denied")
    assert ("doc_ready", "published") not in transitions


def test_reverted_path_records_terminal_reverted(tmp_path: Path) -> None:
    workspace = Workspace(tmp_path / "workspace")

    result = SentinelPipeline(
        workspace,
        git_ops=StubGitOps(),
        baseline_ref="baseline-seed",
        tester=_AlwaysFailingTester(),
    ).run("实现登录接口")

    assert result.status is PipelineOutcome.FAILED
    transitions = _transitions(workspace.pipeline_state_path)
    assert transitions[-1] == ("coded", "reverted")
    assert ("test_failed", "coded") in transitions


def test_blocked_path_records_terminal_blocked(tmp_path: Path) -> None:
    workspace = Workspace(tmp_path / "workspace")

    result = SentinelPipeline(
        workspace,
        git_ops=StubGitOps(),
        tester=_AlwaysFailingTester(),
    ).run("实现登录接口")

    assert result.status is PipelineOutcome.FAILED
    records = _read_lines(workspace.pipeline_state_path)
    assert (records[-1]["from_state"], records[-1]["to_state"]) == ("coded", "blocked")
    assert "基线" in str(records[-1]["detail"])


def test_state_flow_is_not_written_to_audit_log(tmp_path: Path) -> None:
    workspace = Workspace(tmp_path / "workspace")

    SentinelPipeline(workspace, git_ops=StubGitOps()).run("实现登录接口")

    audit_records = _read_lines(workspace.audit_dir / "push-log.jsonl")
    assert len(audit_records) == 1
    for record in audit_records:
        assert "from_state" not in record
        assert "to_state" not in record
