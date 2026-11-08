"""P1 registry 单测：upsert / 查询 / 状态 / WAL。"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from tools.orchestrator.registry import AgentRecord, Registry, utc_now


def _record(role: str = "dev", pid: int = 1234) -> AgentRecord:
    now = utc_now()
    return AgentRecord(
        role=role,
        pid=pid,
        pgid=pid,
        job_id="job-1",
        status="running",
        started_at=now,
        last_heartbeat=now,
        command="dummy",
        updated_at=now,
    )


def test_upsert_and_get(tmp_path: Path) -> None:
    registry = Registry(tmp_path / "registry.sqlite3")

    registry.upsert(_record())

    record = registry.get("dev")
    assert record is not None
    assert record.role == "dev"
    assert record.pid == 1234
    assert record.job_id == "job-1"


def test_upsert_overwrites_same_role(tmp_path: Path) -> None:
    registry = Registry(tmp_path / "registry.sqlite3")
    registry.upsert(_record(pid=111))
    registry.upsert(_record(pid=222))

    record = registry.get("dev")
    assert record is not None
    assert record.pid == 222
    assert len(registry.list()) == 1


def test_missing_role_returns_none(tmp_path: Path) -> None:
    registry = Registry(tmp_path / "registry.sqlite3")

    assert registry.get("nope") is None


def test_list_and_set_status(tmp_path: Path) -> None:
    registry = Registry(tmp_path / "registry.sqlite3")
    registry.upsert(_record("dev", 1))
    registry.upsert(_record("doc", 2))

    assert [record.role for record in registry.list()] == ["dev", "doc"]
    assert registry.set_status("dev", "stopped") is True

    record = registry.get("dev")
    assert record is not None
    assert record.status == "stopped"
    assert registry.set_status("missing", "stopped") is False


def test_heartbeat_updates_timestamp(tmp_path: Path) -> None:
    registry = Registry(tmp_path / "registry.sqlite3")
    registry.upsert(_record())

    assert registry.heartbeat("dev") is True
    assert registry.heartbeat("missing") is False


def test_agent_record_includes_design_fields(tmp_path: Path) -> None:
    registry = Registry(tmp_path / "registry.sqlite3")
    now = utc_now()
    registry.upsert(
        AgentRecord(
            role="dev",
            pid=1,
            pgid=1,
            job_id="job",
            status="running",
            started_at=now,
            last_heartbeat=now,
            command="dummy",
            updated_at=now,
            current_task_id="T-1",
            cost_tokens=12,
            cost_budget=200000,
        )
    )

    record = registry.get("dev")
    assert record is not None
    assert record.current_task_id == "T-1"
    assert record.cost_tokens == 12
    assert record.cost_budget == 200000
    assert registry.set_current_task("dev", "") is True
    cleared = registry.get("dev")
    assert cleared is not None
    assert cleared.current_task_id == ""


def test_legacy_agents_table_is_migrated(tmp_path: Path) -> None:
    path = tmp_path / "legacy.sqlite3"
    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TABLE agents ("
        "role TEXT PRIMARY KEY, pid INTEGER NOT NULL, pgid INTEGER NOT NULL, "
        "job_id TEXT NOT NULL, status TEXT NOT NULL, started_at TEXT NOT NULL, "
        "last_heartbeat TEXT NOT NULL, command TEXT NOT NULL, updated_at TEXT NOT NULL)"
    )
    connection.execute("INSERT INTO agents VALUES('dev',1,1,'job','running','a','b','cmd','c')")
    connection.commit()
    connection.close()

    registry = Registry(path)

    record = registry.get("dev")
    assert record is not None
    assert record.current_task_id == ""
    assert record.cost_tokens == 0
    assert record.cost_budget is None


def test_database_uses_wal(tmp_path: Path) -> None:
    path = tmp_path / "registry.sqlite3"
    Registry(path)

    connection = sqlite3.connect(path)
    try:
        mode = connection.execute("PRAGMA journal_mode").fetchone()
        busy = connection.execute("PRAGMA busy_timeout").fetchone()
    finally:
        connection.close()

    assert mode is not None
    assert str(mode[0]).lower() == "wal"
    assert busy is not None
    assert int(busy[0]) == 5000
