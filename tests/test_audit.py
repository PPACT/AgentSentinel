"""审计日志 JSONL 落盘与字段完整性测试。"""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from agentsentinel.audit import AuditLogger
from agentsentinel.contracts import ActionType, AuditRecord, ScanVerdict, TestStatus

NOW = datetime(2026, 10, 30, 12, 0, tzinfo=UTC)


def _record(action: ActionType = ActionType.PUSH) -> AuditRecord:
    return AuditRecord(
        timestamp=NOW,
        caller="Manager",
        commit_hash="abc123",
        scan_result=ScanVerdict.ALLOW,
        test_result=TestStatus.PASS,
        action_type=action,
    )


def test_record_appends_one_json_line(tmp_path: Path) -> None:
    log_path = tmp_path / "audit" / "push-log.jsonl"

    AuditLogger(log_path).record(_record())

    lines = log_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert payload["caller"] == "Manager"
    assert payload["action_type"] == "push"
    assert payload["scan_result"] == "ALLOW"


def test_records_append_rather_than_overwrite(tmp_path: Path) -> None:
    log_path = tmp_path / "push-log.jsonl"
    logger = AuditLogger(log_path)

    logger.record(_record())
    logger.record(_record(ActionType.REVERT))

    payloads = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    assert [p["action_type"] for p in payloads] == ["push", "revert"]


def test_default_log_path_matches_spec() -> None:
    assert AuditLogger().log_path == Path("workspace/audit/push-log.jsonl")


def test_write_errors_are_not_swallowed(tmp_path: Path) -> None:
    log_path = tmp_path / "as-directory"
    log_path.mkdir()

    with pytest.raises(OSError):
        AuditLogger(log_path).record(_record())


def test_all_spec_fields_are_present(tmp_path: Path) -> None:
    log_path = tmp_path / "push-log.jsonl"

    AuditLogger(log_path).record(_record())

    payload = json.loads(log_path.read_text(encoding="utf-8"))
    assert set(payload) == {
        "timestamp",
        "caller",
        "commit_hash",
        "scan_result",
        "test_result",
        "action_type",
    }
    assert payload["timestamp"] == "2026-10-30T12:00:00Z"
