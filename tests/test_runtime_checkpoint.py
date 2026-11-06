"""Checkpoint / ContextSummary 契约与按角色存储测试。"""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from agentsentinel.contracts import Checkpoint, ContextSummary, SessionState
from agentsentinel.runtime.checkpoint import CheckpointStore, ContextSummaryStore
from agentsentinel.workspace import Workspace

NOW = datetime(2026, 11, 6, 12, 0, tzinfo=UTC)


def _checkpoint(role: str = "Coder", status: SessionState = SessionState.CODED) -> Checkpoint:
    return Checkpoint(
        role=role,
        status=status,
        artifacts=("code/main.py", "reports/test-20261106T120000Z.json"),
        pending_action="run pytest",
        audit_cursor="2026-11-06T12:00:00Z#abcdef123456",
        context_summary_path="reports/context-summary/coder.json",
        created_at=NOW,
    )


def _summary(role: str = "Coder") -> ContextSummary:
    return ContextSummary(
        role=role,
        generated_at=NOW,
        used_tokens=560,
        context_window=1000,
        ratio=0.56,
        summary="compressed",
        pending_action="run pytest",
        strategy="native",
    )


def test_checkpoint_round_trips_and_uses_role_scoped_path(tmp_path: Path) -> None:
    workspace = Workspace(tmp_path / "workspace")
    store = CheckpointStore(workspace)
    checkpoint = _checkpoint()

    path = store.save(checkpoint)

    assert path == workspace.root / "reports" / "checkpoints" / "coder.json"
    assert store.load("Coder") == checkpoint
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["status"] == "coded"
    assert payload["schema_version"] == 1
    assert payload["context_summary_path"] == "reports/context-summary/coder.json"


def test_summary_round_trips_and_uses_role_scoped_path(tmp_path: Path) -> None:
    workspace = Workspace(tmp_path / "workspace")
    store = ContextSummaryStore(workspace)
    summary = _summary()

    path = store.save(summary)

    assert path == workspace.root / "reports" / "context-summary" / "coder.json"
    assert store.load("coder") == summary
    assert json.loads(path.read_text(encoding="utf-8"))["strategy"] == "native"


def test_load_missing_returns_none(tmp_path: Path) -> None:
    workspace = Workspace(tmp_path / "workspace")

    assert CheckpointStore(workspace).load("Coder") is None
    assert ContextSummaryStore(workspace).load("Coder") is None


def test_load_corrupt_returns_none_instead_of_guessing(tmp_path: Path) -> None:
    workspace = Workspace(tmp_path / "workspace")
    checkpoint_path = workspace.checkpoint_path("Coder")
    checkpoint_path.parent.mkdir(parents=True)
    checkpoint_path.write_text("{not-json", encoding="utf-8")

    assert CheckpointStore(workspace).load("Coder") is None


def test_multiple_roles_do_not_overwrite_each_other(tmp_path: Path) -> None:
    workspace = Workspace(tmp_path / "workspace")
    checkpoints = CheckpointStore(workspace)
    summaries = ContextSummaryStore(workspace)

    checkpoints.save(_checkpoint("Coder"))
    checkpoints.save(_checkpoint("Tester", SessionState.TEST_PASSED))
    summaries.save(_summary("Coder"))
    summaries.save(_summary("Tester"))

    coder = checkpoints.load("Coder")
    tester = checkpoints.load("Tester")
    assert coder is not None
    assert tester is not None
    assert coder.status is SessionState.CODED
    assert tester.status is SessionState.TEST_PASSED
    assert summaries.load("Coder") is not None
    assert summaries.load("Tester") is not None


def test_checkpoint_status_must_be_session_state() -> None:
    with pytest.raises(ValidationError):
        Checkpoint(role="Coder", status="running", created_at=NOW)  # type: ignore[arg-type]


def test_checkpoint_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError):
        Checkpoint.model_validate(
            {
                "role": "Coder",
                "status": "coded",
                "created_at": NOW,
                "unexpected": "x",
            }
        )
