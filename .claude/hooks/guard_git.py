"""H1：不可逆命令拦截 + 推送前审查（PreToolUse / Bash）。

只做两件事：
1. deny 强推、删远端、``reset --hard``、``filter-branch``、``clean -fdx``、
   ``git add -A`` 与 ``rm -rf``；
2. ``git push`` 前扫描待推送内容：命中高危内容 → ask 交用户决策；干净 → 静默放行。

按 shell token 解析命令段，避免把引号里的示例文本误判为真实命令。
"""

from __future__ import annotations

import shlex
import subprocess
from pathlib import Path

import common

_OPERATORS = {";", "&&", "||", "|", "&"}
_FORCE_FLAGS = {"-f", "--force", "--force-with-lease", "--force-if-includes"}

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


def _segments(command: str) -> list[list[str]] | None:
    """把 shell 命令切成按 ``;``/``&&``/``||``/``|`` 分隔的 token 段。"""
    try:
        tokens = shlex.split(command, posix=True)
    except ValueError:
        return None
    segments: list[list[str]] = [[]]
    for token in tokens:
        if token in _OPERATORS:
            segments.append([])
        else:
            segments[-1].append(token)
    return [segment for segment in segments if segment]


def _is_wrapper(token: str) -> bool:
    return (
        token == "sudo"
        or token == "env"
        or ("=" in token and token.split("=", 1)[0].isidentifier())
    )


def _command_index(tokens: list[str], name: str) -> int | None:
    for index, token in enumerate(tokens):
        if token == name and all(_is_wrapper(prefix) for prefix in tokens[:index]):
            return index
    return None


def _segment_reason(tokens: list[str]) -> str | None:
    git_index = _command_index(tokens, "git")
    if git_index is not None:
        args = tokens[git_index + 1 :]
        if "push" in args:
            push_args = args[args.index("push") + 1 :]
            if any(arg in _FORCE_FLAGS or arg.startswith("--force") for arg in push_args):
                return "禁止强推（--force / -f / --force-with-lease），需用户明确指令"
            if any(arg in {"--delete", "--mirror"} or arg.startswith(":") for arg in push_args):
                return "禁止删除或镜像远端仓库"
        if "reset" in args and "--hard" in args:
            return "禁止 git reset --hard"
        if "filter-branch" in args:
            return "禁止 git filter-branch"
        if "clean" in args:
            clean_args = args[args.index("clean") + 1 :]
            flags = "".join(arg for arg in clean_args if arg.startswith("-"))
            if all(flag in flags for flag in "fdx"):
                return "禁止 git clean -fdx"
        if "add" in args:
            add_args = args[args.index("add") + 1 :]
            if any(arg in {"-A", "--all", "."} for arg in add_args):
                return "禁止 git add -A / git add . / git add --all，请显式列出文件"

    rm_index = _command_index(tokens, "rm")
    if rm_index is not None:
        rm_args = tokens[rm_index + 1 :]
        flags = "".join(arg for arg in rm_args if arg.startswith("-") and not arg.startswith("--"))
        recursive = "r" in flags.lower() or "--recursive" in rm_args
        force = "f" in flags.lower() or "--force" in rm_args
        if recursive and force:
            return "禁止 rm -rf（递归强制删除）"
    return None


def _has_git_push(segments: list[list[str]]) -> bool:
    for tokens in segments:
        git_index = _command_index(tokens, "git")
        if git_index is not None and "push" in tokens[git_index + 1 :]:
            return True
    return False


def _split_nul(text: str) -> list[str]:
    return [item for item in text.split("\0") if item]


def _resolve_range(cwd: Path) -> str | None:
    upstream = _run_git(cwd, ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"])
    if upstream.returncode != 0:
        return None
    name = upstream.stdout.strip()
    return f"{name}..HEAD" if name else None


def _range_paths(cwd: Path, revision_range: str | None) -> set[str]:
    """push 范围内的路径；无 upstream 时退化为 HEAD 全量树。"""
    if revision_range is not None:
        changed = _run_git(cwd, ["diff", "--name-only", "-z", revision_range])
    else:
        changed = _run_git(cwd, ["ls-tree", "-r", "--name-only", "-z", "HEAD"])
    if changed.returncode != 0:
        raise RuntimeError(changed.stderr.strip() or "无法列出推送范围")
    return set(_split_nul(changed.stdout))


def _read_head_path(cwd: Path, path: str) -> str | None:
    shown = _run_git(cwd, ["show", f"HEAD:{path}"])
    if shown.returncode != 0:
        return None
    if len(shown.stdout) > _MAX_FILE_BYTES:
        raise RuntimeError(f"HEAD:{path} 超过 {_MAX_FILE_BYTES} 字节，无法安全扫描")
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
    for path in sorted(_range_paths(cwd, revision_range)):
        # 测试区完全放行：夹具中的假密钥/PII 不参与推送审查。
        if path.startswith("tests/"):
            continue
        content = _read_head_path(cwd, path)
        if content is None or "\0" in content:
            continue
        reason = common.scan_secrets(content, honor_markers=False)
        if reason is not None:
            return f"HEAD:{path}: {reason}"

    reason = common.scan_secrets(_commit_messages(cwd, revision_range), honor_markers=False)
    if reason is not None:
        return f"提交信息: {reason}"
    return None


def main() -> None:
    payload = common.read_payload()
    if payload.get("tool_name") != "Bash":
        return
    command = common.tool_input(payload).get("command")
    if not isinstance(command, str) or not command:
        return

    segments = _segments(command)
    if segments is None:
        common.fail_closed("H1 无法解析 Bash 命令，按 DENY 处理")
    for segment in segments:
        reason = _segment_reason(segment)
        if reason is not None:
            common.emit_decision("deny", reason)
    if not _has_git_push(segments):
        return

    try:
        hit = _scan_repository(common.payload_cwd(payload))
    except Exception as exc:  # noqa: BLE001 - 扫描失败必须 fail-closed
        common.fail_closed(f"H1 推送前扫描失败，按 DENY 处理：{exc}")
    if hit is not None:
        common.emit_decision("ask", f"推送前扫描发现高风险内容，请确认是否继续：{hit}")


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001 - 安全钩子异常一律 DENY
        common.fail_closed(f"H1 执行异常，按 DENY 处理：{exc}")
