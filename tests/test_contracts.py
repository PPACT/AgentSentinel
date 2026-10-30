"""领域契约的字段、枚举与 JSON 序列化测试。"""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from agentsentinel.contracts import (
    ActionType,
    AuditRecord,
    ScanVerdict,
    TaskState,
    TaskStatus,
    TestReport,
    TestStatus,
)

NOW = datetime(2026, 10, 30, 12, 0, tzinfo=UTC)


def test_test_report_round_trips_as_json() -> None:
    report = TestReport(status=TestStatus.PASS, summary="5 passed", timestamp=NOW)

    payload = report.model_dump(mode="json")

    assert payload == {
        "status": "PASS",
        "summary": "5 passed",
        "details": "",
        "timestamp": "2026-10-30T12:00:00Z",
    }
    assert TestReport.model_validate_json(report.model_dump_json()) == report


def test_scan_verdict_supports_not_run() -> None:
    assert ScanVerdict.NOT_RUN == "NOT_RUN"
    assert ScanVerdict("NOT_RUN") is ScanVerdict.NOT_RUN


def test_test_report_rejects_unknown_status() -> None:
    with pytest.raises(ValidationError):
        TestReport.model_validate({"status": "SKIPPED", "summary": "x", "timestamp": NOW})


def test_audit_record_has_exactly_spec_fields() -> None:
    record = AuditRecord(
        timestamp=NOW,
        caller="Manager",
        commit_hash="abc123",
        scan_result=ScanVerdict.ALLOW,
        test_result=TestStatus.PASS,
        action_type=ActionType.PUSH,
    )

    assert set(record.model_dump(mode="json")) == {
        "timestamp",
        "caller",
        "commit_hash",
        "scan_result",
        "test_result",
        "action_type",
        "reason",
    }
    assert record.reason == ""
    assert record.model_dump(mode="json")["action_type"] == "push"


def test_audit_record_supports_revert() -> None:
    record = AuditRecord(
        timestamp=NOW,
        caller="Orchestrator",
        commit_hash="abc123",
        scan_result=ScanVerdict.ALLOW,
        test_result=TestStatus.FAIL,
        action_type=ActionType.REVERT,
    )

    assert record.model_dump(mode="json")["action_type"] == "revert"


def test_audit_record_rejects_extra_fields() -> None:
    payload = {
        "timestamp": NOW,
        "caller": "Manager",
        "commit_hash": "abc123",
        "scan_result": "ALLOW",
        "test_result": "PASS",
        "action_type": "push",
        "unexpected": "x",
    }
    with pytest.raises(ValidationError):
        AuditRecord.model_validate(payload)


def test_task_status_starts_pending() -> None:
    status = TaskStatus(task_id="task-1", updated_at=NOW)

    assert status.state is TaskState.PENDING
    assert status.detail == ""
