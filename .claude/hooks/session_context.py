"""H5：SessionStart 注入交接摘要（不做阻断，解析失败静默降级）。

注入 STATUS 头四项、交流区未结案标题/计数、归档阈值与最近一次交接。
"""

from __future__ import annotations

import re
from pathlib import Path

import common

_EXCHANGE_DIR = Path("docs/local/交流区")
_STATUS_KEYS = ("当前阶段", "正在做", "等谁", "阻塞")
_HEADING = re.compile(r"^### \[(待裁|已派|已交|已裁)\][^\n]*$", re.MULTILINE)
_LINE_THRESHOLD = 800
_UNARCHIVED_THRESHOLD = 10


def _status_fields(status_text: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for key in (*_STATUS_KEYS, "最近一次交接"):
        match = re.search(rf"^\|\s*{key}\s*\|\s*(.+?)\s*\|\s*$", status_text, re.MULTILINE)
        if match is not None:
            fields[key] = match.group(1)
    return fields


def _entry_is_closed(label: str, body: str) -> bool:
    if label == "待裁":
        return re.search(r"^- 结论", body, re.MULTILINE) is not None or "结论（" in body
    if label == "已派":
        return "回执：已执行 ·" in body
    return re.search(r"(?<!待)认单", body) is not None


def _unclosed_entries(exchange_text: str) -> tuple[list[str], int]:
    headings = list(_HEADING.finditer(exchange_text))
    titles: list[str] = []
    recognized = 0
    for index, heading in enumerate(headings):
        label = heading.group(1)
        end = headings[index + 1].start() if index + 1 < len(headings) else len(exchange_text)
        body = exchange_text[heading.start() : end]
        if label == "已裁":
            continue
        closed = _entry_is_closed(label, body)
        if label == "已交" and closed:
            recognized += 1
        if not closed:
            titles.append(heading.group(0).removeprefix("### ").strip())
    return titles, recognized


def build_context(project_dir: Path) -> str:
    status_path = project_dir / _EXCHANGE_DIR / "STATUS.md"
    exchange_path = project_dir / _EXCHANGE_DIR / "交流区.md"
    if not status_path.is_file() or not exchange_path.is_file():
        return ""
    try:
        status_text = status_path.read_text(encoding="utf-8", errors="replace")
        exchange_text = exchange_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""

    fields = _status_fields(status_text)
    titles, recognized = _unclosed_entries(exchange_text)
    line_count = len(exchange_text.splitlines())

    thresholds: list[str] = []
    if line_count > _LINE_THRESHOLD:
        thresholds.append(f"交流区 {line_count} 行，超过 {_LINE_THRESHOLD} 行阈值")
    if recognized > _UNARCHIVED_THRESHOLD:
        thresholds.append(
            f"已认单未归档 [已交] {recognized} 条，超过 {_UNARCHIVED_THRESHOLD} 条阈值"
        )

    if not titles and not thresholds:
        return ""

    parts = ["## 会话交接（SessionStart 自动注入）"]
    for key in _STATUS_KEYS:
        if key in fields:
            parts.append(f"- {key}：{fields[key]}")
    if titles:
        parts.append(f"- 未结案条目（{len(titles)}）：")
        parts.extend(f"  - {title}" for title in titles)
    if "最近一次交接" in fields:
        parts.append(f"- 最近一次交接：{fields['最近一次交接']}")
    parts.extend(f"- 阈值提醒：{item}" for item in thresholds)
    return "\n".join(parts)


def main() -> None:
    try:
        text = build_context(common.project_root())
    except Exception:  # noqa: BLE001 - 注入失败静默降级，不阻断会话
        return
    if text:
        common.emit_context(text)


if __name__ == "__main__":
    main()
