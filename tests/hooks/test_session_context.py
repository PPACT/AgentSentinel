"""H5 SessionStart 交接注入的正反样本。"""

from __future__ import annotations

import subprocess
from pathlib import Path

from tests.hooks.hook_utils import hook_output, run_hook

_STATUS = """# STATUS

| 项 | 值 |
|---|---|
| 当前阶段 | 测试阶段 |
| 正在做 | 跑测试 |
| 等谁 | dev-agent |
| 阻塞 | 无 |
| 最近一次交接 | 2026-10-31 doc-agent → dev-agent：测试交接 |
"""


def _write_files(project_dir: Path, exchange: str) -> None:
    exchange_dir = project_dir / "docs" / "local" / "交流区"
    exchange_dir.mkdir(parents=True, exist_ok=True)
    (exchange_dir / "STATUS.md").write_text(_STATUS, encoding="utf-8")
    (exchange_dir / "交流区.md").write_text(exchange, encoding="utf-8")


def _run(project_dir: Path) -> subprocess.CompletedProcess[str]:
    return run_hook(
        "session_context.py",
        {},
        project_dir=project_dir,
        env={"SENTINEL_ROLE": ""},
    )


def test_injects_status_and_unclosed_entries(tmp_path: Path) -> None:
    _write_files(
        tmp_path,
        """### [待裁] open question · 2026-10-31 · doc

- 议题：未决

### [已派] closed order · 2026-10-31 · doc

- 回执：已执行 · abc123

### [已交] pending delivery · 2026-10-31 · dev

- commit：abc123

### [已交] acked delivery · 2026-10-31 · dev

- 文档侧认单：ok

### [已裁] resolved decision · 2026-10-31 · user

- 结论：已定
""",
    )

    text = str(hook_output(_run(tmp_path))["additionalContext"])

    assert "测试阶段" in text
    assert "跑测试" in text
    assert "未结案条目（2）" in text
    assert "open question" in text
    assert "pending delivery" in text
    assert "closed order" not in text
    assert "acked delivery" not in text
    assert "最近一次交接：2026-10-31 doc-agent → dev-agent：测试交接" in text


def test_pending_acknowledgment_is_not_closed(tmp_path: Path) -> None:
    _write_files(
        tmp_path,
        """### [已交] pending review · 2026-10-31 · dev

- 文档侧复核：暂不认单，仍需修正
""",
    )

    text = str(hook_output(_run(tmp_path))["additionalContext"])

    assert "未结案条目（1）" in text
    assert "pending review" in text


def test_explicit_acknowledgment_is_closed(tmp_path: Path) -> None:
    _write_files(
        tmp_path,
        """### [已交] acknowledged · 2026-10-31 · dev

- 文档侧认单（2026-10-31）：ok
""",
    )

    result = _run(tmp_path)

    assert result.returncode == 0
    assert result.stdout.strip() == ""


def test_quoted_acknowledgment_phrase_is_not_closed(tmp_path: Path) -> None:
    """正文引用认单措辞（修正说明、测试描述）不得被当作已结案。"""
    _write_files(
        tmp_path,
        """### [已交] quoted phrase · 2026-10-31 · dev

- 修正说明：本条的判定规则是「文档侧认单（」出现即结案。
- 新增 2 条测试：暂不认单 → 未结案；文档侧认单（ → 结案。
""",
    )

    text = str(hook_output(_run(tmp_path))["additionalContext"])

    assert "未结案条目（1）" in text
    assert "quoted phrase" in text


def test_standalone_acknowledgment_lines_are_closed(tmp_path: Path) -> None:
    _write_files(
        tmp_path,
        """### [已交] acked by doc · 2026-10-31 · dev

- 文档侧认单（2026-11-01）：ok

### [已交] acked plainly · 2026-10-31 · dev

- 已认单：ok
""",
    )

    result = _run(tmp_path)

    assert result.returncode == 0
    assert result.stdout.strip() == ""


def test_unacked_variants_are_not_closed(tmp_path: Path) -> None:
    _write_files(
        tmp_path,
        """### [已交] pending a · 2026-10-31 · dev

- 暂不认单：仍需修正

### [已交] pending b · 2026-10-31 · dev

- 待认单
""",
    )

    text = str(hook_output(_run(tmp_path))["additionalContext"])

    assert "未结案条目（2）" in text
    assert "pending a" in text
    assert "pending b" in text


def test_no_noise_when_everything_is_closed(tmp_path: Path) -> None:
    _write_files(
        tmp_path,
        """### [已派] closed order · 2026-10-31 · doc

- 回执：已执行 · abc123
""",
    )

    result = _run(tmp_path)

    assert result.returncode == 0
    assert result.stdout.strip() == ""


def test_missing_files_degrade_silently(tmp_path: Path) -> None:
    result = _run(tmp_path)

    assert result.returncode == 0
    assert result.stdout.strip() == ""


def test_identity_and_register_hint_injected_when_role_set(tmp_path: Path) -> None:
    _write_files(
        tmp_path,
        """### [已派] closed order · 2026-10-31 · doc

- 回执：已执行 · abc123
""",
    )
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "session-role").write_text("dev", encoding="utf-8")

    text = str(hook_output(_run(tmp_path))["additionalContext"])

    assert "身份 role=dev" in text
    assert "register --role dev" in text
    assert "对端为 doc" in text
    assert "交接只发指针" in text


def test_identity_injected_without_exchange_files(tmp_path: Path) -> None:
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "session-role").write_text("doc", encoding="utf-8")

    text = str(hook_output(_run(tmp_path))["additionalContext"])

    assert "role=doc" in text
    assert "register --role doc" in text
    assert "对端为 dev" in text


def test_no_identity_without_role(tmp_path: Path) -> None:
    _write_files(
        tmp_path,
        """### [已派] closed order · 2026-10-31 · doc

- 回执：已执行 · abc123
""",
    )

    result = _run(tmp_path)

    assert result.returncode == 0
    assert result.stdout.strip() == ""


def test_archive_threshold_is_reported(tmp_path: Path) -> None:
    filler = "\n".join(["<!-- filler -->"] * 805)
    _write_files(
        tmp_path,
        filler
        + """

### [已派] closed order · 2026-10-31 · doc

- 回执：已执行 · abc123
""",
    )

    text = str(hook_output(_run(tmp_path))["additionalContext"])

    assert "超过 800 行阈值" in text
