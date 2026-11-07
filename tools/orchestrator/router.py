"""P2 任务 / 持久消息 / 显式 ACK。

状态机：``queued → delivered → running → done | failed | timeout``。
无 ``ack`` 不进入 ``running``；无 ``completed`` 不视为完成。
``steps`` / ``effects`` 只建表，重放与去重逻辑留 P3。
"""

from __future__ import annotations

import json
import os
import re
import secrets
import sqlite3
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from tools.orchestrator.launcher import Launcher, process_alive
from tools.orchestrator.registry import AgentRecord, Registry, utc_now

BUSY_TIMEOUT_MS = 5000
ACK_TIMEOUT_ENV = "SENTINEL_ORCH_ACK_TIMEOUT_SECONDS"
DEFAULT_ACK_TIMEOUT_SECONDS = 120.0
ACTIVE_STATUSES = ("queued", "delivered", "running")
MAX_DELIVERY_ATTEMPTS = 2
_ROLE_PATTERN = re.compile(r"^[a-z0-9_-]+$")
_TASK_COLUMNS = (
    "task_id",
    "from_role",
    "to_role",
    "intent",
    "pointer",
    "acceptance",
    "status",
    "idempotency_key",
    "max_tokens",
    "max_minutes",
    "attempts",
    "created_at",
    "updated_at",
    "ack_at",
    "completed_at",
    "artifacts",
    "cost_tokens",
)


class TaskError(Exception):
    """任务路由错误基类。"""


class TaskNotFound(TaskError):
    pass


class RoleMismatch(TaskError):
    pass


class RoleBusy(TaskError):
    pass


class InvalidTransition(TaskError):
    pass


def ack_timeout_seconds() -> float:
    raw = os.environ.get(ACK_TIMEOUT_ENV, "").strip()
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_ACK_TIMEOUT_SECONDS
    return value if value > 0 else DEFAULT_ACK_TIMEOUT_SECONDS


def _now() -> str:
    # 毫秒精度，供 ACK 超时比较与短超时测试使用。
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _iso_before(seconds: float) -> str:
    return (datetime.now(UTC) - timedelta(seconds=seconds)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _opt_int(row: sqlite3.Row, key: str) -> int | None:
    value = row[key]
    return int(value) if isinstance(value, int) else None


def _opt_str(row: sqlite3.Row, key: str) -> str | None:
    value = row[key]
    return str(value) if isinstance(value, str) else None


@dataclass(frozen=True)
class Task:
    task_id: str
    from_role: str
    to_role: str
    intent: str
    pointer: str
    acceptance: list[str]
    status: str
    idempotency_key: str
    max_tokens: int | None
    max_minutes: int | None
    attempts: int
    created_at: str
    updated_at: str
    ack_at: str | None
    completed_at: str | None
    artifacts: list[str]
    cost_tokens: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _row_to_task(row: sqlite3.Row) -> Task:
    raw_acceptance = row["acceptance"]
    raw_artifacts = row["artifacts"]
    return Task(
        task_id=str(row["task_id"]),
        from_role=str(row["from_role"]),
        to_role=str(row["to_role"]),
        intent=str(row["intent"]),
        pointer=str(row["pointer"]),
        acceptance=_json_list(raw_acceptance),
        status=str(row["status"]),
        idempotency_key=str(row["idempotency_key"]),
        max_tokens=_opt_int(row, "max_tokens"),
        max_minutes=_opt_int(row, "max_minutes"),
        attempts=int(row["attempts"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
        ack_at=_opt_str(row, "ack_at"),
        completed_at=_opt_str(row, "completed_at"),
        artifacts=_json_list(raw_artifacts),
        cost_tokens=int(row["cost_tokens"]),
    )


def _json_list(value: object) -> list[str]:
    if not isinstance(value, str) or not value:
        return []
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return []
    if isinstance(parsed, list):
        return [str(item) for item in parsed]
    return []


class TaskStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.db_path,
            timeout=BUSY_TIMEOUT_MS / 1000.0,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        connection.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
        return connection

    def _init(self) -> None:
        connection = self._connect()
        try:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute(
                "CREATE TABLE IF NOT EXISTS tasks ("
                "task_id TEXT PRIMARY KEY, "
                "from_role TEXT NOT NULL, "
                "to_role TEXT NOT NULL, "
                "intent TEXT NOT NULL, "
                "pointer TEXT NOT NULL, "
                "acceptance TEXT NOT NULL, "
                "status TEXT NOT NULL, "
                "idempotency_key TEXT NOT NULL UNIQUE, "
                "max_tokens INTEGER, "
                "max_minutes INTEGER, "
                "attempts INTEGER NOT NULL DEFAULT 0, "
                "created_at TEXT NOT NULL, "
                "updated_at TEXT NOT NULL, "
                "ack_at TEXT, "
                "completed_at TEXT, "
                "artifacts TEXT NOT NULL DEFAULT '[]', "
                "cost_tokens INTEGER NOT NULL DEFAULT 0)"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS messages ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "task_id TEXT NOT NULL, "
                "type TEXT NOT NULL, "
                "from_role TEXT NOT NULL, "
                "to_role TEXT NOT NULL, "
                "payload_json TEXT NOT NULL, "
                "created_at TEXT NOT NULL)"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS steps ("
                "task_id TEXT NOT NULL, "
                "step_no INTEGER NOT NULL, "
                "status TEXT NOT NULL, "
                "input_ref TEXT, "
                "output_ref TEXT, "
                "updated_at TEXT NOT NULL, "
                "PRIMARY KEY (task_id, step_no))"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS effects ("
                "idem_key TEXT PRIMARY KEY, "
                "task_id TEXT NOT NULL, "
                "action TEXT NOT NULL, "
                "target TEXT, "
                "output_ref TEXT, "
                "created_at TEXT NOT NULL)"
            )
        finally:
            connection.close()

    def get(self, task_id: str) -> Task | None:
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT " + ", ".join(_TASK_COLUMNS) + " FROM tasks WHERE task_id=?",
                (task_id,),
            ).fetchone()
        finally:
            connection.close()
        return _row_to_task(row) if row is not None else None

    def list_tasks(self, *, role: str = "", status: str = "") -> list[Task]:
        clauses: list[str] = []
        params: list[str] = []
        if role:
            clauses.append("(from_role=? OR to_role=?)")
            params.extend([role, role])
        if status:
            clauses.append("status=?")
            params.append(status)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        connection = self._connect()
        try:
            rows = connection.execute(
                "SELECT "
                + ", ".join(_TASK_COLUMNS)
                + " FROM tasks"
                + where
                + " ORDER BY created_at, task_id",
                tuple(params),
            ).fetchall()
        finally:
            connection.close()
        return [_row_to_task(row) for row in rows]

    def messages(self, task_id: str) -> list[dict[str, Any]]:
        connection = self._connect()
        try:
            rows = connection.execute(
                "SELECT id, task_id, type, from_role, to_role, payload_json, created_at "
                "FROM messages WHERE task_id=? ORDER BY id",
                (task_id,),
            ).fetchall()
        finally:
            connection.close()
        messages: list[dict[str, Any]] = []
        for row in rows:
            payload: Any = {}
            try:
                payload = json.loads(str(row["payload_json"]))
            except json.JSONDecodeError:
                payload = {}
            messages.append(
                {
                    "id": int(row["id"]),
                    "task_id": str(row["task_id"]),
                    "type": str(row["type"]),
                    "from_role": str(row["from_role"]),
                    "to_role": str(row["to_role"]),
                    "payload": payload,
                    "created_at": str(row["created_at"]),
                }
            )
        return messages

    def _insert_message(
        self,
        connection: sqlite3.Connection,
        *,
        task_id: str,
        message_type: str,
        from_role: str,
        to_role: str,
        payload: dict[str, Any],
    ) -> None:
        connection.execute(
            "INSERT INTO messages(task_id, type, from_role, to_role, payload_json, created_at) "
            "VALUES(?,?,?,?,?,?)",
            (
                task_id,
                message_type,
                from_role,
                to_role,
                json.dumps(payload, ensure_ascii=False),
                _now(),
            ),
        )

    def create_or_get(
        self,
        *,
        task_id: str,
        from_role: str,
        to_role: str,
        intent: str,
        pointer: str,
        acceptance: list[str],
        idempotency_key: str,
        max_tokens: int | None,
        max_minutes: int | None,
    ) -> tuple[Task, bool]:
        connection = self._connect()
        connection.execute("BEGIN IMMEDIATE")
        try:
            existing = connection.execute(
                "SELECT " + ", ".join(_TASK_COLUMNS) + " FROM tasks WHERE idempotency_key=?",
                (idempotency_key,),
            ).fetchone()
            if existing is not None:
                connection.execute("ROLLBACK")
                return _row_to_task(existing), False
            same_id = connection.execute(
                "SELECT " + ", ".join(_TASK_COLUMNS) + " FROM tasks WHERE task_id=?",
                (task_id,),
            ).fetchone()
            if same_id is not None:
                connection.execute("ROLLBACK")
                return _row_to_task(same_id), False
            busy = connection.execute(
                "SELECT task_id FROM tasks WHERE to_role=? AND status IN (?,?,?) LIMIT 1",
                (to_role, *ACTIVE_STATUSES),
            ).fetchone()
            if busy is not None:
                connection.execute("ROLLBACK")
                raise RoleBusy(f"role busy: {to_role} ({busy['task_id']})")
            now = _now()
            connection.execute(
                "INSERT INTO tasks(" + ", ".join(_TASK_COLUMNS) + ") "
                "VALUES(" + ", ".join("?" for _ in _TASK_COLUMNS) + ")",
                (
                    task_id,
                    from_role,
                    to_role,
                    intent,
                    pointer,
                    json.dumps(acceptance, ensure_ascii=False),
                    "queued",
                    idempotency_key,
                    max_tokens,
                    max_minutes,
                    0,
                    now,
                    now,
                    None,
                    None,
                    "[]",
                    0,
                ),
            )
            connection.execute("COMMIT")
        except Exception:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        finally:
            connection.close()
        created = self.get(task_id)
        if created is None:  # pragma: no cover - 防御
            raise TaskNotFound(task_id)
        return created, True

    def mark_delivered(self, task_id: str, *, message: str = "need") -> Task:
        connection = self._connect()
        now = _now()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT " + ", ".join(_TASK_COLUMNS) + " FROM tasks WHERE task_id=?",
                (task_id,),
            ).fetchone()
            if row is None:
                raise TaskNotFound(task_id)
            task = _row_to_task(row)
            if task.status == "delivered":
                connection.execute("ROLLBACK")
                return task
            if task.status != "queued":
                raise InvalidTransition(f"cannot deliver from {task.status}")
            connection.execute(
                "UPDATE tasks SET status='delivered', attempts=attempts+1, updated_at=? "
                "WHERE task_id=?",
                (now, task_id),
            )
            self._insert_message(
                connection,
                task_id=task_id,
                message_type=message,
                from_role=task.from_role,
                to_role=task.to_role,
                payload={
                    "intent": task.intent,
                    "pointer": task.pointer,
                    "acceptance": task.acceptance,
                },
            )
            connection.execute("COMMIT")
        except Exception:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        finally:
            connection.close()
        updated = self.get(task_id)
        if updated is None:  # pragma: no cover - 防御
            raise TaskNotFound(task_id)
        return updated

    def redeliver(self, task_id: str) -> Task:
        connection = self._connect()
        now = _now()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT " + ", ".join(_TASK_COLUMNS) + " FROM tasks WHERE task_id=?",
                (task_id,),
            ).fetchone()
            if row is None:
                raise TaskNotFound(task_id)
            task = _row_to_task(row)
            if task.status != "delivered":
                raise InvalidTransition(f"cannot redeliver from {task.status}")
            connection.execute(
                "UPDATE tasks SET attempts=attempts+1, updated_at=? WHERE task_id=?",
                (now, task_id),
            )
            self._insert_message(
                connection,
                task_id=task_id,
                message_type="need_retry",
                from_role="orchestrator",
                to_role=task.to_role,
                payload={"attempt": task.attempts + 1},
            )
            connection.execute("COMMIT")
        except Exception:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        finally:
            connection.close()
        updated = self.get(task_id)
        if updated is None:  # pragma: no cover - 防御
            raise TaskNotFound(task_id)
        return updated

    def mark_running(self, task_id: str, role: str) -> Task:
        connection = self._connect()
        now = _now()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT " + ", ".join(_TASK_COLUMNS) + " FROM tasks WHERE task_id=?",
                (task_id,),
            ).fetchone()
            if row is None:
                raise TaskNotFound(task_id)
            task = _row_to_task(row)
            if role != task.to_role:
                raise RoleMismatch(f"role {role} cannot ack task for {task.to_role}")
            if task.status in {"running", "done"}:
                connection.execute("ROLLBACK")
                return task
            if task.status != "delivered":
                raise InvalidTransition(f"cannot ack from {task.status}")
            connection.execute(
                "UPDATE tasks SET status='running', ack_at=?, updated_at=? WHERE task_id=?",
                (now, now, task_id),
            )
            self._insert_message(
                connection,
                task_id=task_id,
                message_type="ack",
                from_role=task.to_role,
                to_role=task.from_role,
                payload={},
            )
            connection.execute("COMMIT")
        except Exception:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        finally:
            connection.close()
        updated = self.get(task_id)
        if updated is None:  # pragma: no cover - 防御
            raise TaskNotFound(task_id)
        return updated

    def mark_completed(
        self,
        task_id: str,
        *,
        role: str,
        artifacts: list[str],
        cost_tokens: int,
    ) -> Task:
        connection = self._connect()
        now = _now()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT " + ", ".join(_TASK_COLUMNS) + " FROM tasks WHERE task_id=?",
                (task_id,),
            ).fetchone()
            if row is None:
                raise TaskNotFound(task_id)
            task = _row_to_task(row)
            if role != task.to_role:
                raise RoleMismatch(f"role {role} cannot complete task for {task.to_role}")
            if task.status == "done":
                connection.execute("ROLLBACK")
                return task
            if task.status != "running":
                raise InvalidTransition(f"cannot complete from {task.status}")
            connection.execute(
                "UPDATE tasks SET status='done', completed_at=?, updated_at=?, "
                "artifacts=?, cost_tokens=? WHERE task_id=?",
                (
                    now,
                    now,
                    json.dumps(artifacts, ensure_ascii=False),
                    cost_tokens,
                    task_id,
                ),
            )
            self._insert_message(
                connection,
                task_id=task_id,
                message_type="completed",
                from_role=task.to_role,
                to_role=task.from_role,
                payload={"artifacts": artifacts, "cost_tokens": cost_tokens},
            )
            connection.execute("COMMIT")
        except Exception:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        finally:
            connection.close()
        updated = self.get(task_id)
        if updated is None:  # pragma: no cover - 防御
            raise TaskNotFound(task_id)
        return updated

    def mark_timeout(self, task_id: str) -> Task:
        connection = self._connect()
        now = _now()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT " + ", ".join(_TASK_COLUMNS) + " FROM tasks WHERE task_id=?",
                (task_id,),
            ).fetchone()
            if row is None:
                raise TaskNotFound(task_id)
            task = _row_to_task(row)
            if task.status == "timeout":
                connection.execute("ROLLBACK")
                return task
            if task.status != "delivered":
                raise InvalidTransition(f"cannot timeout from {task.status}")
            connection.execute(
                "UPDATE tasks SET status='timeout', updated_at=? WHERE task_id=?",
                (now, task_id),
            )
            self._insert_message(
                connection,
                task_id=task_id,
                message_type="timeout",
                from_role="orchestrator",
                to_role=task.from_role,
                payload={"attempts": task.attempts},
            )
            connection.execute("COMMIT")
        except Exception:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        finally:
            connection.close()
        updated = self.get(task_id)
        if updated is None:  # pragma: no cover - 防御
            raise TaskNotFound(task_id)
        return updated

    def mark_failed(self, task_id: str, reason: str) -> Task:
        connection = self._connect()
        now = _now()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT " + ", ".join(_TASK_COLUMNS) + " FROM tasks WHERE task_id=?",
                (task_id,),
            ).fetchone()
            if row is None:
                raise TaskNotFound(task_id)
            task = _row_to_task(row)
            if task.status in {"done", "failed", "timeout"}:
                connection.execute("ROLLBACK")
                return task
            connection.execute(
                "UPDATE tasks SET status='failed', updated_at=? WHERE task_id=?",
                (now, task_id),
            )
            self._insert_message(
                connection,
                task_id=task_id,
                message_type="failed",
                from_role="orchestrator",
                to_role=task.from_role,
                payload={"reason": reason},
            )
            connection.execute("COMMIT")
        except Exception:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        finally:
            connection.close()
        updated = self.get(task_id)
        if updated is None:  # pragma: no cover - 防御
            raise TaskNotFound(task_id)
        return updated

    def due_for_ack(self, cutoff: str) -> list[Task]:
        connection = self._connect()
        try:
            rows = connection.execute(
                "SELECT "
                + ", ".join(_TASK_COLUMNS)
                + " FROM tasks WHERE status='delivered' AND updated_at <= ?",
                (cutoff,),
            ).fetchall()
        finally:
            connection.close()
        return [_row_to_task(row) for row in rows]


def new_task_id() -> str:
    return datetime.now(UTC).strftime("T-%Y%m%d%H%M%S-") + secrets.token_hex(3)


class Router:
    def __init__(
        self,
        *,
        store: TaskStore,
        registry: Registry,
        launcher: Launcher,
        token: str,
        orchestrator_url: str = "",
    ) -> None:
        self.store = store
        self.registry = registry
        self.launcher = launcher
        self.token = token
        self.orchestrator_url = orchestrator_url

    def _healthy_worker(self, role: str) -> int:
        worker = self.launcher.get(role)
        if worker is not None and process_alive(worker.pid):
            return worker.pid
        record = self.registry.get(role)
        if record is not None and process_alive(record.pid):
            return record.pid
        return 0

    def _remember_agent(self, role: str, pid: int, pgid: int, job_id: str) -> None:
        now = utc_now()
        self.registry.upsert(
            AgentRecord(
                role=role,
                pid=pid,
                pgid=pgid,
                job_id=job_id,
                status="running",
                started_at=now,
                last_heartbeat=now,
                command=f"dummy_worker --role {role}",
                updated_at=now,
            )
        )

    def need(self, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        from_role = _role_value(payload, "from_role")
        to_role = _role_value(payload, "to_role")
        intent = _required_text(payload, "intent")
        pointer = _required_text(payload, "pointer")
        acceptance = _string_list(payload.get("acceptance"))
        budget = payload.get("budget")
        max_tokens = _opt_int_value(budget, "max_tokens")
        max_minutes = _opt_int_value(budget, "max_minutes")
        raw_key = payload.get("idempotency_key")
        idempotency_key = raw_key.strip() if isinstance(raw_key, str) else ""
        raw_task_id = payload.get("task_id")
        task_id = raw_task_id.strip() if isinstance(raw_task_id, str) else ""
        if not task_id:
            task_id = new_task_id()
        if not idempotency_key:
            idempotency_key = f"{task_id}:need"

        task, created = self.store.create_or_get(
            task_id=task_id,
            from_role=from_role,
            to_role=to_role,
            intent=intent,
            pointer=pointer,
            acceptance=acceptance,
            idempotency_key=idempotency_key,
            max_tokens=max_tokens,
            max_minutes=max_minutes,
        )
        if not created:
            return 200, {"task": task.to_dict(), "idempotent": True}

        try:
            if not self._healthy_worker(to_role):
                worker = self.launcher.spawn_dummy(
                    to_role,
                    extra_env={
                        "TASK_ID": task.task_id,
                        "ORCHESTRATOR_URL": self.orchestrator_url,
                        "ORCHESTRATOR_TOKEN": self.token,
                    },
                )
                self._remember_agent(to_role, worker.pid, worker.pgid, worker.job_id)
            delivered = self.store.mark_delivered(task.task_id)
        except Exception as exc:
            self.store.mark_failed(task.task_id, type(exc).__name__)
            raise
        return 200, {"task": delivered.to_dict(), "idempotent": False}

    def ack(self, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        task_id = _required_text(payload, "task_id")
        role = _required_text(payload, "role").casefold()
        task = self.store.mark_running(task_id, role)
        return 200, {"task": task.to_dict()}

    def completed(self, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        task_id = _required_text(payload, "task_id")
        role = _required_text(payload, "role").casefold()
        artifacts = _string_list(payload.get("artifacts"))
        cost_tokens = _int_value(payload.get("cost_tokens"))
        task = self.store.mark_completed(
            task_id, role=role, artifacts=artifacts, cost_tokens=cost_tokens
        )
        return 200, {"task": task.to_dict()}

    def tasks(self, *, role: str = "", status: str = "") -> dict[str, Any]:
        records = self.store.list_tasks(role=role.casefold(), status=status)
        return {"tasks": [record.to_dict() for record in records]}

    def task_detail(self, task_id: str) -> dict[str, Any]:
        task = self.store.get(task_id)
        if task is None:
            raise TaskNotFound(task_id)
        return {"task": task.to_dict(), "messages": self.store.messages(task_id)}

    def check_timeouts(self, timeout_seconds: float) -> list[dict[str, Any]]:
        actions: list[dict[str, Any]] = []
        for task in self.store.due_for_ack(_iso_before(timeout_seconds)):
            if task.attempts < MAX_DELIVERY_ATTEMPTS:
                updated = self.store.redeliver(task.task_id)
                actions.append(
                    {"task_id": task.task_id, "action": "redeliver", "attempts": updated.attempts}
                )
            else:
                updated = self.store.mark_timeout(task.task_id)
                actions.append({"task_id": task.task_id, "action": "timeout"})
        return actions


def _role_value(payload: dict[str, Any], key: str) -> str:
    value = _required_text(payload, key).casefold()
    if _ROLE_PATTERN.fullmatch(value) is None:
        raise ValueError(f"invalid {key}: {value}")
    return value


def _required_text(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} is required")
    return value.strip()


def _string_list(value: object) -> list[str]:
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    return []


def _int_value(value: object) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return 0


def _opt_int_value(mapping: object, key: str) -> int | None:
    if not isinstance(mapping, dict):
        return None
    value = mapping.get(key)
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return None
