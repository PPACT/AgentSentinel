"""Workspace 配置中心：SQLite 持久化，绝不写入用户项目文件。"""

from __future__ import annotations

import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tools.orchestrator.engines import adapter_for

DEFAULT_BUDGET_TOKENS = 200_000


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _int(value: object, default: int) -> int:
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return default


class WorkspaceStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.control_home = db_path.parent / "orchestrator"
        self.workspace_root = db_path.parent / "workspaces"
        self._lock = threading.RLock()
        self._init()

    def _daemon_home(self, workspace_id: int) -> Path:
        if workspace_id == 1:
            return self.control_home
        return self.workspace_root / str(workspace_id)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=5)
        connection.row_factory = sqlite3.Row
        return connection

    def _init(self) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS workspaces ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "name TEXT NOT NULL, "
                "project_path TEXT NOT NULL, "
                "command TEXT NOT NULL, "
                "engine TEXT NOT NULL, "
                "wake_prompt TEXT NOT NULL DEFAULT '', "
                "wake_prompt_file TEXT NOT NULL DEFAULT '', "
                "budget_tokens INTEGER NOT NULL DEFAULT 200000, "
                "active INTEGER NOT NULL DEFAULT 0, "
                "daemon_home TEXT NOT NULL DEFAULT '', "
                "created_at TEXT NOT NULL, "
                "updated_at TEXT NOT NULL)"
            )
            columns = {
                str(row["name"])
                for row in connection.execute("PRAGMA table_info(workspaces)").fetchall()
            }
            if "daemon_home" not in columns:
                connection.execute(
                    "ALTER TABLE workspaces ADD COLUMN daemon_home TEXT NOT NULL DEFAULT ''"
                )

    def _to_dict(self, row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        adapter = adapter_for(str(data.get("engine", "")))
        data["engine_label"] = adapter.label
        data["capabilities"] = adapter.capabilities()
        data["wake_prompt_source"] = "file" if data.get("wake_prompt_file") else "text"
        if not data.get("daemon_home"):
            data["daemon_home"] = str(self._daemon_home(int(data.get("id", 0))))
        return data

    def list(self) -> list[dict[str, Any]]:
        with self._lock, self._connect() as connection:
            rows = connection.execute("SELECT * FROM workspaces ORDER BY id").fetchall()
        return [self._to_dict(row) for row in rows]

    def get(self, workspace_id: int) -> dict[str, Any] | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM workspaces WHERE id=?", (workspace_id,)
            ).fetchone()
        return self._to_dict(row) if row is not None else None

    def active(self) -> dict[str, Any] | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM workspaces WHERE active=1 ORDER BY id LIMIT 1"
            ).fetchone()
            if row is None:
                row = connection.execute("SELECT * FROM workspaces ORDER BY id LIMIT 1").fetchone()
        return self._to_dict(row) if row is not None else None

    def seed_default(self, project_path: str) -> dict[str, Any]:
        with self._lock:
            if self.list():
                active = self.active()
                if active is not None:
                    return active
            default = {
                "name": "AgentSentinel",
                "project_path": project_path,
                "command": "claude",
                "engine": "claude",
                "wake_prompt": "",
                "wake_prompt_file": "",
                "budget_tokens": DEFAULT_BUDGET_TOKENS,
            }
            return self.save(default, activate=True)

    def save(self, payload: dict[str, Any], *, activate: bool = False) -> dict[str, Any]:
        workspace_id = _int(payload.get("id"), 0)
        name = str(payload.get("name", "")).strip()
        project_path = str(payload.get("project_path", "")).strip()
        engine = str(payload.get("engine", "claude")).strip().lower() or "claude"
        adapter = adapter_for(engine)
        command = str(payload.get("command", "")).strip() or adapter.default_command
        wake_prompt = str(payload.get("wake_prompt", "") or "")
        wake_prompt_file = str(payload.get("wake_prompt_file", "") or "").strip()
        budget_tokens = max(1, _int(payload.get("budget_tokens"), DEFAULT_BUDGET_TOKENS))
        if not name:
            raise ValueError("workspace name is required")
        if not project_path:
            raise ValueError("project_path is required")
        if not command:
            raise ValueError("command is required")
        now = _now()
        with self._lock, self._connect() as connection:
            if workspace_id > 0:
                existing = connection.execute(
                    "SELECT id FROM workspaces WHERE id=?", (workspace_id,)
                ).fetchone()
                if existing is None:
                    raise ValueError(f"unknown workspace: {workspace_id}")
                connection.execute(
                    "UPDATE workspaces SET name=?, project_path=?, command=?, engine=?, "
                    "wake_prompt=?, wake_prompt_file=?, budget_tokens=?, updated_at=? WHERE id=?",
                    (
                        name,
                        project_path,
                        command,
                        engine,
                        wake_prompt,
                        wake_prompt_file,
                        budget_tokens,
                        now,
                        workspace_id,
                    ),
                )
                saved_id = workspace_id
            else:
                cursor = connection.execute(
                    "INSERT INTO workspaces (name, project_path, command, engine, wake_prompt, "
                    "wake_prompt_file, budget_tokens, active, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?)",
                    (
                        name,
                        project_path,
                        command,
                        engine,
                        wake_prompt,
                        wake_prompt_file,
                        budget_tokens,
                        now,
                        now,
                    ),
                )
                saved_id = int(cursor.lastrowid or 0)
            if activate:
                connection.execute("UPDATE workspaces SET active=0")
                connection.execute("UPDATE workspaces SET active=1 WHERE id=?", (saved_id,))
            has_active = connection.execute(
                "SELECT 1 FROM workspaces WHERE active=1 LIMIT 1"
            ).fetchone()
            if has_active is None:
                connection.execute("UPDATE workspaces SET active=1 WHERE id=?", (saved_id,))
            saved_home = connection.execute(
                "SELECT daemon_home FROM workspaces WHERE id=?", (saved_id,)
            ).fetchone()
            if saved_home is not None and not saved_home["daemon_home"]:
                connection.execute(
                    "UPDATE workspaces SET daemon_home=? WHERE id=?",
                    (str(self._daemon_home(saved_id)), saved_id),
                )
            connection.commit()
            row = connection.execute("SELECT * FROM workspaces WHERE id=?", (saved_id,)).fetchone()
        if row is None:
            raise ValueError("workspace save failed")
        return self._to_dict(row)

    def delete(self, workspace_id: int) -> bool:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT active FROM workspaces WHERE id=?", (workspace_id,)
            ).fetchone()
            if row is None:
                return False
            connection.execute("DELETE FROM workspaces WHERE id=?", (workspace_id,))
            if int(row["active"]) == 1:
                next_row = connection.execute(
                    "SELECT id FROM workspaces ORDER BY id LIMIT 1"
                ).fetchone()
                if next_row is not None:
                    connection.execute(
                        "UPDATE workspaces SET active=1 WHERE id=?", (next_row["id"],)
                    )
            connection.commit()
        return True

    def activate(self, workspace_id: int) -> dict[str, Any]:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT id FROM workspaces WHERE id=?", (workspace_id,)
            ).fetchone()
            if row is None:
                raise ValueError(f"unknown workspace: {workspace_id}")
            connection.execute("UPDATE workspaces SET active=0")
            connection.execute("UPDATE workspaces SET active=1 WHERE id=?", (workspace_id,))
            connection.commit()
            saved = connection.execute(
                "SELECT * FROM workspaces WHERE id=?", (workspace_id,)
            ).fetchone()
        if saved is None:
            raise ValueError("workspace activate failed")
        return self._to_dict(saved)
