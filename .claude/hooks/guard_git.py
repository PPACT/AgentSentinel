"""H1：危险 git 命令拦截 + 推送前脱敏扫描（PreToolUse / Bash）。

``git push`` 前扫描工作区改动、push 范围内各文件在 HEAD 的内容与提交信息；
扫描失败按 DENY 处理（fail-closed）。``DENY`` 为终态，不重试、不绕过。
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import common

_FORCE_PUSH = re.compile(
    r"\bgit\s+push\b[^;&|]*?(?:--force(?:-with-lease|-if-includes)?|(?<!\S)-f\b)"
)
_DELETE_PUSH = re.compile(r"\bgit\s+push\b[^;&|]*?(?:--delete|--mirror|(?<!\S):\S+)")
_RESET_HARD = re.compile(r"\bgit\s+reset\b[^;&|]*?--hard\b")
_FILTER_BRANCH = re.compile(r"\bgit\s+filter-branch\b")
_GIT_ADD = re.compile(r"\bgit\s+add\b([^;&|]*)")
_GIT_CLEAN = re.compile(r"\bgit\s+clean\b([^;&|]*)")
_GIT_PUSH = re.compile(r"\bgit\s+push\b")

_MAX_FILE_BYTES = 5_000_000
_GIT_TIMEOUT_SECONDS = 60


def _run_git(cwd: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=_GIT_TIMEOUT_SECONDS,
    )


def _dangerous_command_reason(command: str) -> str | None:
    if _FORCE_PUSH.search(command):
        return "禁止强推（--force / -f / --force-with-lease），需用户明确指令"
    if _DELETE_PUSH.search(command):
        return "禁止删除或镜像远端仓库"
    if _RESET_HARD.search(command):
        return "禁止 git reset --hard"
    if _FILTER_BRANCH.search(command):
        return "禁止 git filter-branch"
    for match in _GIT_CLEAN.finditer(command):
        flags = "".join(token for token in match.group(1).split() if token.startswith("-"))
        if all(flag in flags for flag in "fdx"):
            return "禁止 git clean -fdx"
    for match in _GIT_ADD.finditer(command):
        tokens = match.group(1).split()
        if any(token in {"-A", "--all"} or token == "." for token in tokens):
            return "禁止 git add -A / git add . / git add --all，请显式列出文件"
    return None


def _split_nul(text: str) -> list[str]:
    return [item for item in text.split("\0") if item]


def _resolve_range(cwd: Path) -> str | None:
    upstream = _run_git(cwd, ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"])
    if upstream.returncode != 0:
        return None
    name = upstream.stdout.strip()
    return f"{name}..HEAD" if name else None


def _paths_to_scan(cwd: Path, revision_range: str | None) -> set[str]:
    if revision_range is not None:
        changed = _run_git(cwd, ["diff", "--name-only", "-z", revision_range])
    else:
        changed = _run_git(cwd, ["ls-tree", "-r", "--name-only", "-z", "HEAD"])
    if changed.returncode != 0:
        raise RuntimeError(changed.stderr.strip() or "无法列出推送范围")

    paths = set(_split_nul(changed.stdout))
    status = _run_git(cwd, ["status", "--porcelain", "-z"])
    if status.returncode != 0:
        raise RuntimeError(status.stderr.strip() or "git status 失败")
    for entry in _split_nul(status.stdout):
        if len(entry) >= 4:
            paths.add(entry[3:])
    return paths


def _read_path(cwd: Path, path: str) -> str:
    file_path = cwd / path
    if file_path.is_file():
        if file_path.stat().st_size > _MAX_FILE_BYTES:
            raise RuntimeError(f"{path} 超过 {_MAX_FILE_BYTES} 字节，无法安全扫描")
        return file_path.read_text(encoding="utf-8", errors="replace")
    shown = _run_git(cwd, ["show", f"HEAD:{path}"])
    if shown.returncode != 0:
        return ""
    return shown.stdout


def _commit_messages(cwd: Path, revision_range: str | None) -> str:
    args = ["log", "--format=%B"]
    args.extend([revision_range] if revision_range is not None else ["-n", "1", "HEAD"])
    result = _run_git(cwd, args)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "无法读取提交信息")
    return result.stdout


def _scan_repository(cwd: Path) -> str | None:
    inside = _run_git(cwd, ["rev-parse", "--is-inside-work-tree"])
    if inside.returncode != 0 or inside.stdout.strip() != "true":
        raise RuntimeError(f"{cwd} 不是 git 工作区")

    revision_range = _resolve_range(cwd)
    for path in sorted(_paths_to_scan(cwd, revision_range)):
        content = _read_path(cwd, path)
        if "\0" in content:
            continue
        reason = common.scan_secrets(content)
        if reason is not None:
            return f"{path}: {reason}"

    reason = common.scan_secrets(_commit_messages(cwd, revision_range))
    if reason is not None:
        return f"提交信息: {reason}"
    return None


def main() -> None:
    payload = common.read_payload()
    if payload.get("tool_name") != "Bash":
        return
    command = common.tool_input(payload).get("command")
    if not isinstance(command, str) or "git" not in command:
        return

    reason = _dangerous_command_reason(command)
    if reason is not None:
        common.emit_decision("deny", reason)
    if _GIT_PUSH.search(command) is None:
        return

    try:
        hit = _scan_repository(common.payload_cwd(payload))
    except Exception as exc:  # noqa: BLE001 - 扫描失败必须 fail-closed
        common.fail_closed(f"H1 推送前扫描失败，按 DENY 处理：{exc}")
    if hit is not None:
        common.emit_decision("deny", f"推送前脱敏扫描命中：{hit}")


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001 - 安全钩子异常一律 DENY
        common.fail_closed(f"H1 执行异常，按 DENY 处理：{exc}")
