"""H1：不可逆命令拦截 + 推送前审查（PreToolUse / Bash）。

只做两件事：
1. deny 强推、删远端、``reset --hard``、``filter-branch``、``clean`` 的
   ``-f`` 与 ``-d``/``-x`` 组合、``git add`` 的 ``-A`` 组合与 ``rm -rf``；
2. ``git push`` 前扫描待推送内容：命中高危内容 → ask 交用户决策；干净 → 静默放行。
   显式 refspec（``git push origin <branch>`` / ``<local>:<remote>``）按被推送的
   ref 范围扫描，而不是当前分支 upstream。

按 shell token 解析命令段与 ``git`` 子命令，避免把引号里的示例文本或提交信息
误判为真实命令。
"""

from __future__ import annotations

import re
import shlex
import subprocess
from pathlib import Path

import common

_OPERATORS = {";", "&&", "||", "|", "&"}
_FORCE_FLAGS = {"-f", "--force", "--force-with-lease", "--force-if-includes"}
# git 全局选项中带一个取值的项，用于定位真正的子命令（如 ``git -c k=v push``）。
_GIT_VALUE_OPTIONS = {
    "-C",
    "-c",
    "--git-dir",
    "--work-tree",
    "--namespace",
    "--exec-path",
    "--super-prefix",
    "--config-env",
}
# git push 自身带一个取值的选项，用于挑出 repository 与 refspec。
_PUSH_VALUE_OPTIONS = {"-o", "--push-option", "--repo", "--receive-pack", "--exec"}
# shell 重定向 token：单独操作符（如 ``>``、``2>``）需连同目标一起跳过；
# 自带目标的形式（如 ``2>&1``、``>/dev/null``）只跳过自身。
_REDIRECT_ONLY = re.compile(r"^\d*(?:>>?|<<?|<<<|&>>?|>&|<&)$")
_REDIRECT_WITH_TARGET = re.compile(r"^\d*(?:>>?|<<?|<<<|&>>?|>&|<&).+$")

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
        # punctuation_chars 让紧贴前一个 token 的 ``;``/``|`` 也单独成 token；
        # 不能把 ``&`` 加进来，否则 ``2>&1`` 会被拆成 ``2>``/``&``/``1``。
        lexer = shlex.shlex(command, posix=True, punctuation_chars=";|")
        lexer.whitespace_split = True
        lexer.commenters = ""
        tokens = list(lexer)
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


def _git_subcommand(tokens: list[str], git_index: int) -> tuple[str | None, list[str]]:
    """返回 ``git`` 的子命令与其参数；跳过全局选项，避免把参数当子命令。"""
    args = tokens[git_index + 1 :]
    index = 0
    while index < len(args):
        token = args[index]
        if token in _GIT_VALUE_OPTIONS:
            index += 2
            continue
        if token.startswith("-"):
            index += 1
            continue
        return token, args[index + 1 :]
    return None, []


def _short_flag_letters(args: list[str]) -> set[str]:
    """收集组合短选项里的字母，如 ``-dfx`` → ``{"d", "f", "x"}``。"""
    letters: set[str] = set()
    for arg in args:
        if arg.startswith("-") and not arg.startswith("--"):
            letters.update(arg[1:])
    return letters


def _segment_reason(tokens: list[str]) -> str | None:
    git_index = _command_index(tokens, "git")
    if git_index is not None:
        subcommand, args = _git_subcommand(tokens, git_index)
        if subcommand == "push":
            if any(arg in _FORCE_FLAGS or arg.startswith("--force") for arg in args):
                return "禁止强推（--force / -f / --force-with-lease），需用户明确指令"
            if any(arg in {"--delete", "--mirror"} or arg.startswith(":") for arg in args):
                return "禁止删除或镜像远端仓库"
        elif subcommand == "reset" and "--hard" in args:
            return "禁止 git reset --hard"
        elif subcommand == "filter-branch":
            return "禁止 git filter-branch"
        elif subcommand == "clean":
            letters = _short_flag_letters(args)
            long_flags = {arg for arg in args if arg in {"--dry-run", "--force"}}
            dry_run = "n" in letters or "--dry-run" in long_flags
            force = "f" in letters or "--force" in long_flags
            if not dry_run and force and ("d" in letters or "x" in letters):
                return "禁止 git clean 的 force 与 -d/-x 组合（如 -fd、-dfx、--force -d）"
        elif subcommand == "add":
            if any(
                arg in {".", "--all"}
                or (arg.startswith("-") and not arg.startswith("--") and "A" in arg)
                for arg in args
            ):
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


def _push_rests(segments: list[list[str]]) -> list[list[str]]:
    """返回每条真实 ``git push`` 命令的参数；没有则返回空列表。"""
    rests: list[list[str]] = []
    for tokens in segments:
        git_index = _command_index(tokens, "git")
        if git_index is None:
            continue
        subcommand, rest = _git_subcommand(tokens, git_index)
        if subcommand == "push":
            rests.append(rest)
    return rests


def _push_refspecs(rest: list[str]) -> tuple[str, list[str]] | None:
    """从 push 参数中挑出 repository 与显式 refspec；无显式 refspec 返回 None。"""
    positionals: list[str] = []
    index = 0
    while index < len(rest):
        token = rest[index]
        if _REDIRECT_ONLY.match(token):
            index += 2
            continue
        if _REDIRECT_WITH_TARGET.match(token):
            index += 1
            continue
        if token in _PUSH_VALUE_OPTIONS:
            index += 2
            continue
        if token.startswith("-"):
            index += 1
            continue
        positionals.append(token)
        index += 1
    if len(positionals) < 2:
        return None
    return positionals[0], positionals[1:]


def _parse_refspec(refspec: str) -> tuple[str, str] | None:
    refspec = refspec.removeprefix("+")
    if not refspec or refspec.startswith(":"):
        return None
    if ":" in refspec:
        src, dst = refspec.split(":", 1)
    else:
        src = dst = refspec
    return (src, dst) if src else None


def _tracking_ref(remote: str, dst: str) -> str | None:
    """远端分支对应的 tracking ref；URL 形式远端或 tag 推送返回 None。"""
    if ":" in remote or "/" in remote:
        return None
    name = dst.removeprefix("refs/heads/")
    if name.startswith("refs/") or not name or name == "HEAD":
        return None
    return f"refs/remotes/{remote}/{name}"


def _resolve_sha(cwd: Path, revision: str) -> str | None:
    resolved = _run_git(cwd, ["rev-parse", "--verify", "--quiet", f"{revision}^{{commit}}"])
    if resolved.returncode != 0:
        return None
    return resolved.stdout.strip() or None


def _push_scopes(cwd: Path, rest: list[str]) -> list[tuple[str, str | None]]:
    """把显式 refspec 解析成 (revision, 扫描范围)；无 refspec 返回空列表。"""
    parsed = _push_refspecs(rest)
    if parsed is None:
        return []
    remote, refspecs = parsed
    head_sha = _resolve_sha(cwd, "HEAD")
    scopes: list[tuple[str, str | None]] = []
    for refspec in refspecs:
        parts = _parse_refspec(refspec)
        if parts is None:
            continue
        src, dst = parts
        src_sha = _resolve_sha(cwd, src)
        if src_sha is None:
            raise RuntimeError(f"无法解析推送源 {src}")
        revision = "HEAD" if src_sha == head_sha else src
        tracking = _tracking_ref(remote, dst)
        if tracking is not None and _resolve_sha(cwd, tracking) is not None:
            scopes.append((revision, f"{tracking}..{revision}"))
        else:
            scopes.append((revision, None))
    return scopes


def _split_nul(text: str) -> list[str]:
    return [item for item in text.split("\0") if item]


def _resolve_range(cwd: Path) -> str | None:
    upstream = _run_git(cwd, ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"])
    if upstream.returncode != 0:
        return None
    name = upstream.stdout.strip()
    return f"{name}..HEAD" if name else None


def _range_paths(cwd: Path, revision_range: str | None, revision: str = "HEAD") -> set[str]:
    """推送范围内的路径；无可用范围时退化为该 revision 的全量树。"""
    if revision_range is not None:
        changed = _run_git(cwd, ["diff", "--name-only", "-z", revision_range])
    else:
        changed = _run_git(cwd, ["ls-tree", "-r", "--name-only", "-z", revision])
    if changed.returncode != 0:
        raise RuntimeError(changed.stderr.strip() or "无法列出推送范围")
    return set(_split_nul(changed.stdout))


def _read_revision_path(cwd: Path, revision: str, path: str) -> str | None:
    shown = _run_git(cwd, ["show", f"{revision}:{path}"])
    if shown.returncode != 0:
        return None
    if len(shown.stdout) > _MAX_FILE_BYTES:
        raise RuntimeError(f"{revision}:{path} 超过 {_MAX_FILE_BYTES} 字节，无法安全扫描")
    return shown.stdout


def _commit_messages(
    cwd: Path, revision_range: str | None, revision: str, *, only_tip: bool
) -> str:
    args = ["log", "--format=%B"]
    if revision_range is not None:
        args.append(revision_range)
    elif only_tip:
        args.extend(["-n", "1", revision])
    else:
        args.append(revision)
    result = _run_git(cwd, args)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "无法读取提交信息")
    return result.stdout


def _scan_repository(cwd: Path, push_rest: list[str]) -> str | None:
    inside = _run_git(cwd, ["rev-parse", "--is-inside-work-tree"])
    if inside.returncode != 0 or inside.stdout.strip() != "true":
        raise RuntimeError(f"{cwd} 不是 git 工作区")

    scopes = _push_scopes(cwd, push_rest)
    if scopes:
        targets = [
            (revision, path)
            for revision, revision_range in scopes
            for path in _range_paths(cwd, revision_range, revision)
        ]
        messages = [
            _commit_messages(cwd, revision_range, revision, only_tip=False)
            for revision, revision_range in scopes
        ]
    else:
        # 无显式 refspec：沿用当前分支 upstream 口径（无 upstream 时扫 HEAD）。
        revision_range = _resolve_range(cwd)
        targets = [("HEAD", path) for path in _range_paths(cwd, revision_range, "HEAD")]
        messages = [_commit_messages(cwd, revision_range, "HEAD", only_tip=True)]

    seen: set[tuple[str, str]] = set()
    for revision, path in targets:
        if (revision, path) in seen:
            continue
        seen.add((revision, path))
        # 测试区完全放行：夹具中的假密钥/PII 不参与推送审查。
        if path.startswith("tests/"):
            continue
        content = _read_revision_path(cwd, revision, path)
        if content is None or "\0" in content:
            continue
        reason = common.scan_secrets(content, honor_markers=False)
        if reason is not None:
            return f"{revision}:{path}: {reason}"

    for message in messages:
        reason = common.scan_secrets(message, honor_markers=False)
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
    push_rests = _push_rests(segments)
    if not push_rests:
        return

    try:
        cwd = common.payload_cwd(payload)
        for push_rest in push_rests:
            hit = _scan_repository(cwd, push_rest)
            if hit is not None:
                common.emit_decision("ask", f"推送前扫描发现高风险内容，请确认是否继续：{hit}")
    except Exception as exc:  # noqa: BLE001 - 扫描失败必须 fail-closed
        common.fail_closed(f"H1 推送前扫描失败，按 DENY 处理：{exc}")


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001 - 安全钩子异常一律 DENY
        common.fail_closed(f"H1 执行异常，按 DENY 处理：{exc}")
