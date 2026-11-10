"""P5 预算 / 用量账本 / 观测页。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from pathlib import Path
from typing import Any, cast

import pytest
from tools.orchestrator.cli import _render_observability
from tools.orchestrator.launcher import (
    Launcher,
    Worker,
    hidden_creationflags,
    parse_claude_usage,
)
from tools.orchestrator.registry import Registry
from tools.orchestrator.router import (
    RoleBudgetExceeded,
    Router,
    TaskStore,
    _now,
)

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows Job Object")

ROOT = Path(__file__).resolve().parents[2]


def _router(tmp_path: Path) -> tuple[Router, TaskStore, Launcher]:
    db = tmp_path / "db.sqlite3"
    registry = Registry(db)
    store = TaskStore(db)
    launcher = Launcher(tmp_path / "orch")
    router = Router(
        store=store,
        registry=registry,
        launcher=launcher,
        token="token",
        orchestrator_url="http://127.0.0.1:1",
    )
    return router, store, launcher


def _record_usage(
    store: TaskStore, *, role: str = "dev", billed: int, task_id: str = "T-usage"
) -> None:
    store.record_usage(
        task_id=task_id,
        role=role,
        model="test-model",
        input_tokens=billed,
        output_tokens=0,
        cache_creation_tokens=0,
        cache_read_tokens=0,
        cost_usd=0.01,
        duration_ms=100,
        status="completed",
    )


def test_parse_claude_usage(tmp_path: Path) -> None:
    log = tmp_path / "worker.log"
    log.write_text(
        "some stderr noise\n"
        + json.dumps(
            {
                "type": "result",
                "usage": {
                    "input_tokens": 120,
                    "output_tokens": 30,
                    "cache_creation_input_tokens": 5,
                    "cache_read_input_tokens": 900,
                },
                "total_cost_usd": 0.045,
                "duration_ms": 1234,
                "is_error": False,
                "modelUsage": {"test-model": {}},
            }
        )
        + "\n",
        encoding="utf-8",
    )

    usage = parse_claude_usage(log)

    assert usage is not None
    assert usage["input_tokens"] == 120
    assert usage["output_tokens"] == 30
    assert usage["cache_creation_tokens"] == 5
    assert usage["cache_read_tokens"] == 900
    assert usage["cost_usd"] == 0.045
    assert usage["duration_ms"] == 1234
    assert usage["model"] == "test-model"
    assert usage["status"] == "completed"


def test_usage_record_and_summary(tmp_path: Path) -> None:
    _, store, _ = _router(tmp_path)
    _record_usage(store, billed=100)
    _record_usage(store, billed=50)

    usage = store.list_usage(role="dev")
    summary = store.usage_summary(role="dev")

    assert len(usage) == 2
    assert store.billed_for_role_day("dev", _now()[:10]) == 150
    assert summary["totals"]["runs"] == 2
    assert summary["totals"]["billed_tokens"] == 150
    assert summary["totals"]["cost_usd"] == pytest.approx(0.02)


def test_task_budget_warning_then_exceeded(tmp_path: Path) -> None:
    router, store, launcher = _router(tmp_path)
    try:
        task, _ = store.create_or_get(
            task_id="T-budget",
            from_role="doc",
            to_role="dev",
            intent="x",
            pointer="p",
            acceptance=[],
            idempotency_key="budget",
            max_tokens=100,
            max_minutes=30,
        )
        store.mark_delivered(task.task_id)

        _record_usage(store, billed=85, task_id=task.task_id)
        router.apply_task_budget(task.task_id)
        warning = store.get(task.task_id)
        assert warning is not None
        assert warning.status == "delivered"
        assert store.has_message(task.task_id, "budget_warning")

        _record_usage(store, billed=20, task_id=task.task_id)
        router.apply_task_budget(task.task_id)
        exceeded = store.get(task.task_id)
        assert exceeded is not None
        assert exceeded.status == "budget_exceeded"
        assert store.has_message(task.task_id, "budget_exceeded")
    finally:
        launcher.stop_all()


def test_role_budget_rejects_new_need(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SENTINEL_ORCH_ROLE_BUDGET_TOKENS", "10")
    router, store, launcher = _router(tmp_path)
    try:
        _record_usage(store, billed=10)

        with pytest.raises(RoleBudgetExceeded):
            router.need(
                {
                    "from_role": "doc",
                    "to_role": "dev",
                    "intent": "x",
                    "pointer": "p",
                    "idempotency_key": "over-budget",
                }
            )
    finally:
        launcher.stop_all()


def test_task_budget_defaults_from_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SENTINEL_ORCH_TASK_MAX_TOKENS", "1234")
    monkeypatch.setenv("SENTINEL_ORCH_TASK_MAX_MINUTES", "7")
    monkeypatch.setenv("SENTINEL_ORCH_ALLOW_MANAGED_WORKERS", "1")
    router, _, launcher = _router(tmp_path)
    try:
        code, body = router.need(
            {
                "from_role": "doc",
                "to_role": "dev",
                "intent": "x",
                "pointer": "p",
                "idempotency_key": "defaults",
                "execution": "managed",
            }
        )
        assert code == 200
        assert body["task"]["max_tokens"] == 1234
        assert body["task"]["max_minutes"] == 7
    finally:
        launcher.stop_all()


def test_minutes_budget_marks_exceeded(tmp_path: Path) -> None:
    router, store, launcher = _router(tmp_path)
    try:
        task, _ = store.create_or_get(
            task_id="T-minutes",
            from_role="doc",
            to_role="dev",
            intent="x",
            pointer="p",
            acceptance=[],
            idempotency_key="minutes",
            max_tokens=1000,
            max_minutes=0,
        )
        store.mark_delivered(task.task_id)

        actions = router.check_task_budgets()

        assert any(action["action"] == "budget_exceeded" for action in actions)
        current = store.get(task.task_id)
        assert current is not None
        assert current.status == "budget_exceeded"
    finally:
        launcher.stop_all()


def test_observability_and_text_render(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SENTINEL_ORCH_ALLOW_MANAGED_WORKERS", "1")
    router, _, launcher = _router(tmp_path)
    try:
        code, _ = router.need(
            {
                "from_role": "doc",
                "to_role": "dev",
                "intent": "x",
                "pointer": "p",
                "idempotency_key": "obs",
                "execution": "managed",
            }
        )
        assert code == 200
        data = router.observability()

        assert set(data) == {
            "agents",
            "tasks",
            "recent_messages",
            "usage",
            "residual",
            "budgets",
        }
        assert data["agents"]
        assert data["budgets"]["role_budget_tokens"] == 200000
        text = _render_observability(data)
        assert "agents:" in text
        assert "residual:" in text
        assert "budgets:" in text
    finally:
        launcher.stop_all()


def _read_info(home: Path) -> dict[str, Any] | None:
    try:
        data = json.loads((home / "daemon.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _decode(raw: bytes) -> dict[str, Any]:
    data = json.loads(raw.decode("utf-8") or "{}")
    return data if isinstance(data, dict) else {}


def _api(
    home: Path,
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
) -> tuple[int, dict[str, Any]]:
    info = _read_info(home)
    assert info is not None
    headers = {"Authorization": "Bearer " + (home / "token").read_text(encoding="utf-8").strip()}
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    if body is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(
        f"http://{info['host']}:{info['port']}{path}",
        data=body,
        headers=headers,
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return int(response.status), _decode(response.read())
    except urllib.error.HTTPError as exc:
        return int(exc.code), _decode(exc.read())


@pytest.fixture
def daemon(tmp_path: Path) -> Iterator[Path]:
    home = tmp_path / "orch"
    home.mkdir()
    environment = os.environ.copy()
    environment["SENTINEL_ORCH_HOME"] = str(home)
    environment["SENTINEL_ORCH_ROLE_BUDGET_TOKENS"] = "10"
    environment["PYTHONUTF8"] = "1"
    log_path = tmp_path / "daemon.log"
    with log_path.open("w", encoding="utf-8") as stream:
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "tools.orchestrator.service",
                "--serve",
                "--port",
                "0",
                "--home",
                str(home),
            ],
            cwd=ROOT,
            env=environment,
            stdout=stream,
            stderr=subprocess.STDOUT,
            creationflags=hidden_creationflags(),
        )
    try:
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if _read_info(home) is not None:
                try:
                    status, body = _api(home, "GET", "/health")
                except OSError:
                    pass
                else:
                    if status == 200 and body.get("ok"):
                        break
            time.sleep(0.1)
        else:
            raise AssertionError("daemon did not become healthy")
        yield home
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()


def test_usage_endpoint_and_role_budget_429(daemon: Path) -> None:
    store = TaskStore(daemon / "registry.sqlite3")
    _record_usage(store, billed=10)

    status, body = _api(
        daemon,
        "POST",
        "/need",
        {
            "from_role": "doc",
            "to_role": "dev",
            "intent": "x",
            "pointer": "p",
            "idempotency_key": "daemon-budget",
        },
    )
    assert status == 429, body

    status, usage = _api(daemon, "GET", "/usage?role=dev")
    assert status == 200
    assert usage["summary"]["totals"]["billed_tokens"] == 10

    status, observability = _api(daemon, "GET", "/observability")
    assert status == 200
    assert "daemon" in observability
    assert "residual" in observability
    assert observability["budgets"]["role_budget_tokens"] == 10


def test_record_usage_created_at_format() -> None:
    assert _now().endswith("Z")


class _ExitedProcess:
    stdin = None

    def poll(self) -> int:
        return 0

    def wait(self, timeout: float | None = None) -> int:
        return 0


def test_real_completed_defers_reclaim_and_watchdog_records_usage(
    tmp_path: Path,
) -> None:
    router, store, launcher = _router(tmp_path)
    task, _ = store.create_or_get(
        task_id="T-real",
        from_role="doc",
        to_role="dev",
        intent="x",
        pointer="p",
        acceptance=[],
        idempotency_key="real-usage",
        max_tokens=100000,
        max_minutes=30,
    )
    store.mark_delivered(task.task_id)
    store.mark_running(task.task_id, "dev")
    log = tmp_path / "dev-real.log"
    log.write_text(
        json.dumps(
            {
                "type": "result",
                "usage": {"input_tokens": 120, "output_tokens": 30},
                "total_cost_usd": 0.045,
                "duration_ms": 1000,
                "is_error": False,
                "modelUsage": {"test-model": {}},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    worker = Worker(
        role="dev",
        pid=999999,
        pgid=999999,
        job_id="fake",
        child_pid=0,
        job_handle=0,
        process=cast(Any, _ExitedProcess()),
        pid_file=tmp_path / "dev.json",
        log_path=log,
        task_id=task.task_id,
        mode="real",
    )
    launcher._workers["dev"] = worker
    try:
        code, body = router.completed({"task_id": task.task_id, "role": "dev", "artifacts": []})
        assert code == 200
        assert body["reclaim"]["deferred"] is True
        assert launcher.get("dev") is worker

        actions = router.watch_workers()

        assert any(
            action["action"] == "worker_exit" and action["usage_recorded"] for action in actions
        )
        usage = store.list_usage(task_id=task.task_id)
        assert len(usage) == 1
        assert usage[0]["input_tokens"] == 120
        assert usage[0]["cost_usd"] == 0.045
        assert launcher.get("dev") is None
    finally:
        launcher.stop_all()
