"""P0-4：SQLite WAL + busy_timeout 并发读写、唯一约束与事务。

用法：``uv run python tools/orchestrator/probes/p0_sqlite.py``
证据：``.claude/orchestrator/p0/sqlite.json``
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
EVIDENCE_DIR = ROOT / ".claude" / "orchestrator" / "p0"
DB_PATH = EVIDENCE_DIR / "p0.sqlite3"
BUSY_TIMEOUT_MS = 5000


def _connect() -> sqlite3.Connection:
    connection = sqlite3.connect(DB_PATH, timeout=BUSY_TIMEOUT_MS / 1000.0, isolation_level=None)
    connection.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
    return connection


def _count(connection: sqlite3.Connection, key: str) -> int:
    row = connection.execute(
        "SELECT COUNT(*) FROM messages WHERE idempotency_key=?", (key,)
    ).fetchone()
    return int(row[0]) if row else 0


def _init() -> dict[str, Any]:
    for suffix in ("", "-wal", "-shm"):
        Path(f"{DB_PATH}{suffix}").unlink(missing_ok=True)
    connection = _connect()
    try:
        mode_row = connection.execute("PRAGMA journal_mode=WAL").fetchone()
        busy_row = connection.execute("PRAGMA busy_timeout").fetchone()
        connection.execute(
            "CREATE TABLE messages ("
            "id INTEGER PRIMARY KEY, "
            "idempotency_key TEXT NOT NULL UNIQUE, "
            "status TEXT NOT NULL)"
        )
    finally:
        connection.close()
    return {
        "journal_mode": str(mode_row[0]).lower() if mode_row else "",
        "busy_timeout_ms": int(busy_row[0]) if busy_row else 0,
        "sqlite_version": sqlite3.sqlite_version,
    }


def _transaction_test() -> dict[str, Any]:
    connection = _connect()
    try:
        connection.execute("BEGIN")
        connection.execute(
            "INSERT INTO messages(idempotency_key,status) VALUES(?,?)",
            ("tx-rollback", "pending"),
        )
        connection.execute("ROLLBACK")
        rolled_back = _count(connection, "tx-rollback")

        connection.execute("BEGIN")
        connection.execute(
            "INSERT INTO messages(idempotency_key,status) VALUES(?,?)",
            ("tx-commit", "pending"),
        )
        connection.execute("COMMIT")
        committed = _count(connection, "tx-commit")
    finally:
        connection.close()
    return {"rollback_count": rolled_back, "commit_count": committed}


def _concurrent_writers() -> dict[str, Any]:
    barrier = threading.Barrier(2)
    outcomes: list[dict[str, Any]] = []
    guard = threading.Lock()

    def writer(name: str) -> None:
        connection = _connect()
        started = time.monotonic()
        outcome = "unknown"
        try:
            barrier.wait(timeout=5)
            try:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    "INSERT INTO messages(idempotency_key,status) VALUES(?,?)",
                    ("dup-key", "pending"),
                )
                time.sleep(0.3)
                connection.execute("COMMIT")
                outcome = "committed"
            except sqlite3.IntegrityError:
                connection.execute("ROLLBACK")
                outcome = "integrity_error"
            except sqlite3.OperationalError as exc:
                outcome = f"operational_error: {exc}"
        finally:
            connection.close()
        with guard:
            outcomes.append(
                {
                    "writer": name,
                    "outcome": outcome,
                    "elapsed_seconds": round(time.monotonic() - started, 3),
                }
            )

    threads = [
        threading.Thread(target=writer, args=("a",)),
        threading.Thread(target=writer, args=("b",)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    connection = _connect()
    try:
        duplicate_rows = _count(connection, "dup-key")
    finally:
        connection.close()
    return {
        "outcomes": sorted(outcomes, key=lambda item: str(item["writer"])),
        "dup_key_rows": duplicate_rows,
    }


def _reader_during_writer() -> dict[str, Any]:
    ready = threading.Event()
    release = threading.Event()
    writer_errors: list[str] = []

    def writer() -> None:
        connection = _connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "INSERT INTO messages(idempotency_key,status) VALUES(?,?)",
                ("reader-key", "pending"),
            )
            ready.set()
            release.wait(timeout=5)
            connection.execute("COMMIT")
        except sqlite3.Error as exc:
            writer_errors.append(str(exc))
        finally:
            connection.close()

    thread = threading.Thread(target=writer)
    thread.start()
    ready.wait(timeout=5)
    connection = _connect()
    started = time.monotonic()
    read_ok = False
    count = -1
    read_error = ""
    try:
        row = connection.execute("SELECT COUNT(*) FROM messages").fetchone()
        count = int(row[0]) if row else -1
        read_ok = True
    except sqlite3.OperationalError as exc:
        read_error = str(exc)
    finally:
        connection.close()
    elapsed = time.monotonic() - started
    release.set()
    thread.join(timeout=5)
    return {
        "read_ok": read_ok,
        "count_visible_to_reader": count,
        "read_elapsed_seconds": round(elapsed, 3),
        "read_error": read_error,
        "writer_errors": writer_errors,
    }


def run() -> dict[str, Any]:
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    init = _init()
    transaction = _transaction_test()
    concurrent = _concurrent_writers()
    reader = _reader_during_writer()

    committed = sum(1 for item in concurrent["outcomes"] if item["outcome"] == "committed")
    integrity = sum(1 for item in concurrent["outcomes"] if item["outcome"] == "integrity_error")
    passed = (
        init["journal_mode"] == "wal"
        and init["busy_timeout_ms"] == BUSY_TIMEOUT_MS
        and transaction["rollback_count"] == 0
        and transaction["commit_count"] == 1
        and committed == 1
        and integrity == 1
        and concurrent["dup_key_rows"] == 1
        and bool(reader["read_ok"])
    )
    return {
        "probe": "sqlite_wal",
        "passed": passed,
        "init": init,
        "transaction": transaction,
        "concurrent_writers": concurrent,
        "reader_during_writer": reader,
        "notes": [
            "WAL 模式：写事务进行时另一连接仍可读（读不阻塞写、写不阻塞读）",
            "busy_timeout=5000ms：第二个写者等待锁而不是立即 SQLITE_BUSY",
            "idempotency_key UNIQUE：并发重复写入恰好一次成功、另一次 IntegrityError",
        ],
    }


def main() -> int:
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    record = run()
    path = EVIDENCE_DIR / "sqlite.json"
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"passed": record["passed"], "evidence": str(path)}, ensure_ascii=False))
    return 0 if record["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
