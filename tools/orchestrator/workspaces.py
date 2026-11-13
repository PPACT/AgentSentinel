"""Workspace 配置中心：SQLite 持久化，绝不写入用户项目文件。"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Sequence
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
    def __init__(self, db_path: Path, *, control_home: Path | None = None) -> None:
        self.db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.control_home = (
            Path(control_home) if control_home is not None else db_path.parent / "orchestrator"
        )
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
            connection.execute(
                "CREATE TABLE IF NOT EXISTS workspace_agents ("
                "workspace_id INTEGER NOT NULL, "
                "role TEXT NOT NULL, "
                "wake_prompt_type TEXT NOT NULL DEFAULT 'text', "
                "wake_prompt_content TEXT NOT NULL DEFAULT '', "
                "updated_at TEXT NOT NULL, "
                "PRIMARY KEY (workspace_id, role))"
            )
            # 旧数据迁移：Workspace 级唤醒词下放给该工作区的 local 角色。
            legacy_rows = connection.execute(
                "SELECT id, wake_prompt, wake_prompt_file FROM workspaces"
            ).fetchall()
            for legacy in legacy_rows:
                file_value = str(legacy["wake_prompt_file"] or "").strip()
                text_value = str(legacy["wake_prompt"] or "").strip()
                if not file_value and not text_value:
                    continue
                exists = connection.execute(
                    "SELECT 1 FROM workspace_agents WHERE workspace_id=? AND role='local'",
                    (legacy["id"],),
                ).fetchone()
                if exists is None:
                    prompt_type = "file" if file_value else "text"
                    content = file_value or text_value
                    connection.execute(
                        "INSERT INTO workspace_agents "
                        "(workspace_id, role, wake_prompt_type, wake_prompt_content, updated_at) "
                        "VALUES (?, 'local', ?, ?, ?)",
                        (legacy["id"], prompt_type, content, _now()),
                    )
                connection.execute(
                    "UPDATE workspaces SET wake_prompt='', wake_prompt_file='' WHERE id=?",
                    (legacy["id"],),
                )

    def _to_dict(self, row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        adapter = adapter_for(str(data.get("engine", "")))
        data["engine_label"] = adapter.label
        data["capabilities"] = adapter.capabilities()
        data.pop("wake_prompt", None)
        data.pop("wake_prompt_file", None)
        if not data.get("daemon_home"):
            data["daemon_home"] = str(self._daemon_home(int(data.get("id", 0))))
        return data

    @staticmethod
    def _agent_to_dict(row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        content = str(data.get("wake_prompt_content") or "").strip()
        data["configured"] = bool(content)
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
                    "SELECT id, budget_tokens FROM workspaces WHERE id=?", (workspace_id,)
                ).fetchone()
                if existing is None:
                    raise ValueError(f"unknown workspace: {workspace_id}")
                if "budget_tokens" not in payload:
                    budget_tokens = int(existing["budget_tokens"] or DEFAULT_BUDGET_TOKENS)
                connection.execute(
                    "UPDATE workspaces SET name=?, project_path=?, command=?, engine=?, "
                    "budget_tokens=?, updated_at=? WHERE id=?",
                    (
                        name,
                        project_path,
                        command,
                        engine,
                        budget_tokens,
                        now,
                        workspace_id,
                    ),
                )
                saved_id = workspace_id
            else:
                cursor = connection.execute(
                    "INSERT INTO workspaces (name, project_path, command, engine, "
                    "budget_tokens, active, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, 0, ?, ?)",
                    (
                        name,
                        project_path,
                        command,
                        engine,
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
            connection.execute("DELETE FROM workspace_agents WHERE workspace_id=?", (workspace_id,))
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

    def list_agents(self, workspace_id: int) -> Sequence[dict[str, Any]]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM workspace_agents WHERE workspace_id=? ORDER BY role",
                (workspace_id,),
            ).fetchall()
        return [self._agent_to_dict(row) for row in rows]

    def get_agent(self, workspace_id: int, role: str) -> dict[str, Any] | None:
        role = role.strip().casefold()
        if workspace_id <= 0 or not role:
            return None
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM workspace_agents WHERE workspace_id=? AND role=?",
                (workspace_id, role),
            ).fetchone()
        return self._agent_to_dict(row) if row is not None else None

    def save_agent(self, payload: dict[str, Any]) -> dict[str, Any]:
        workspace_id = _int(payload.get("workspace_id"), 0)
        role = str(payload.get("role", "")).strip().casefold()
        prompt_type = str(payload.get("wake_prompt_type", "text")).strip().lower()
        content = str(payload.get("wake_prompt_content", "") or "")
        if prompt_type not in ("text", "file"):
            raise ValueError("wake_prompt_type must be text or file")
        if workspace_id <= 0:
            raise ValueError("workspace_id is required")
        if not role:
            raise ValueError("role is required")
        now = _now()
        with self._lock, self._connect() as connection:
            workspace = connection.execute(
                "SELECT id FROM workspaces WHERE id=?", (workspace_id,)
            ).fetchone()
            if workspace is None:
                raise ValueError(f"unknown workspace: {workspace_id}")
            connection.execute(
                "INSERT INTO workspace_agents "
                "(workspace_id, role, wake_prompt_type, wake_prompt_content, updated_at) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(workspace_id, role) DO UPDATE SET "
                "wake_prompt_type=excluded.wake_prompt_type, "
                "wake_prompt_content=excluded.wake_prompt_content, "
                "updated_at=excluded.updated_at",
                (workspace_id, role, prompt_type, content, now),
            )
            connection.commit()
            row = connection.execute(
                "SELECT * FROM workspace_agents WHERE workspace_id=? AND role=?",
                (workspace_id, role),
            ).fetchone()
        if row is None:
            raise ValueError("workspace agent save failed")
        return self._agent_to_dict(row)

    def delete_agent(self, workspace_id: int, role: str) -> bool:
        role = role.strip().casefold()
        if workspace_id <= 0 or not role:
            return False
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM workspace_agents WHERE workspace_id=? AND role=?",
                (workspace_id, role),
            )
            connection.commit()
            return bool(cursor.rowcount)

    def set_budget(self, workspace_id: int, budget_tokens: object) -> dict[str, Any]:
        budget = _int(budget_tokens, 0)
        if budget <= 0:
            raise ValueError("budget_tokens must be positive")
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                "UPDATE workspaces SET budget_tokens=?, updated_at=? WHERE id=?",
                (budget, _now(), workspace_id),
            )
            connection.commit()
            if cursor.rowcount == 0:
                raise ValueError(f"unknown workspace: {workspace_id}")
            row = connection.execute(
                "SELECT * FROM workspaces WHERE id=?", (workspace_id,)
            ).fetchone()
        if row is None:
            raise ValueError(f"unknown workspace: {workspace_id}")
        return self._to_dict(row)

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
