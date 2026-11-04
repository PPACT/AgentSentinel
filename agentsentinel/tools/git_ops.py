"""受控 Git 工具：GitPython 封装、权限/调用者校验与推送前扫描。

测试通过注入 remote_push / reset_runner 模拟远端与回退，不触网、不动真实仓库。
"""

from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Protocol

from git import Repo

from agentsentinel.governance.permissions import (
    ACTION_PUSH,
    ACTION_REVERT,
    ORCHESTRATOR_NAME,
    Mandate,
    PermissionDecision,
    PermissionPolicy,
    verify_mandate,
)
from agentsentinel.governance.sanitizer import Sanitizer
from agentsentinel.workspace import ARTIFACT_DIRS

# 只提交 workspace 下的产物目录，禁止 git add -A 把仓库其它改动一并带走。
DEFAULT_ADD_PATHS: tuple[str, ...] = tuple(f"workspace/{name}" for name in ARTIFACT_DIRS)


class ScanDeniedError(Exception):
    """推送前扫描返回 DENY；调用方必须终止，不得重试。"""


class GitOps(Protocol):
    def commit(self, message: str, *, caller: str = "Manager") -> str: ...

    def push(self, *, mandate: Mandate, caller: str = "Manager", payload: str = "") -> None: ...

    def tag_baseline(self, name: str, *, caller: str = "Manager") -> None: ...

    def reset_to_baseline(
        self, ref: str, *, mandate: Mandate, caller: str = ORCHESTRATOR_NAME
    ) -> None: ...


class StubGitOps:
    """只记录调用并返回假 hash，绝不执行 git。用于离线编排。"""

    def __init__(self, *, target_branch: str = "main") -> None:
        self.calls: list[tuple[str, str]] = []
        self._counter = 0
        self._target_branch = target_branch

    def commit(self, message: str, *, caller: str = "Manager") -> str:
        self._counter += 1
        self.calls.append(("commit", message))
        return f"stub{self._counter:07d}"

    def push(self, *, mandate: Mandate, caller: str = "Manager", payload: str = "") -> None:
        self._verify_mandate(mandate, caller=caller, action=ACTION_PUSH)
        self.calls.append(("push", ""))

    def tag_baseline(self, name: str, *, caller: str = "Manager") -> None:
        self.calls.append(("tag_baseline", name))

    def reset_to_baseline(
        self, ref: str, *, mandate: Mandate, caller: str = ORCHESTRATOR_NAME
    ) -> None:
        self._verify_mandate(mandate, caller=caller, action=ACTION_REVERT)
        self.calls.append(("reset_to_baseline", ref))

    def _verify_mandate(self, mandate: Mandate, *, caller: str, action: str) -> None:
        """离线比对票据，行为与 GitPythonOps 一致，但不触网、不动仓库。"""
        decision = verify_mandate(
            mandate,
            caller=caller,
            action=action,
            target_branch=self._target_branch,
        )
        if decision is not PermissionDecision.ALLOW:
            raise PermissionError(
                f"{caller} 的 Mandate 不匹配 {action}（目标分支 {self._target_branch}）"
            )


class GitPythonOps:
    """GitPython 实现；每个动作先过权限矩阵，推送前再扫一次内容。"""

    def __init__(
        self,
        repo_path: Path | str,
        *,
        policy: PermissionPolicy,
        sanitizer: Sanitizer | None = None,
        remote_push: Callable[[Repo], None] | None = None,
        reset_runner: Callable[[Repo, str], None] | None = None,
        remote_name: str = "origin",
        add_paths: Sequence[str] = DEFAULT_ADD_PATHS,
    ) -> None:
        self._repo_path = Path(repo_path)
        self._policy = policy
        self._sanitizer = sanitizer
        self._remote_push = remote_push
        self._reset_runner = reset_runner
        self._remote_name = remote_name
        self._add_paths = tuple(add_paths)

    def commit(self, message: str, *, caller: str = "Manager") -> str:
        self._authorize("git_commit", caller)
        repo = Repo(self._repo_path)
        existing = [path for path in self._add_paths if (self._repo_path / path).exists()]
        if not existing:
            raise ValueError(f"没有可提交的工作区产物: {list(self._add_paths)}")
        repo.git.add("--", *existing)
        return repo.index.commit(message).hexsha

    def push(self, *, mandate: Mandate, caller: str = "Manager", payload: str = "") -> None:
        self._authorize("git_push", caller)
        repo = Repo(self._repo_path)
        self._verify_mandate(mandate, caller=caller, action=ACTION_PUSH, repo=repo)
        if self._sanitizer is not None:
            scan = self._sanitizer.scan(payload)
            if not scan.allowed:
                raise ScanDeniedError(scan.reason)

        if self._remote_push is not None:
            self._remote_push(repo)
        else:
            repo.remote(self._remote_name).push()

    def tag_baseline(self, name: str, *, caller: str = "Manager") -> None:
        self._authorize("git_tag_baseline", caller)
        Repo(self._repo_path).create_tag(name)

    def reset_to_baseline(
        self, ref: str, *, mandate: Mandate, caller: str = ORCHESTRATOR_NAME
    ) -> None:
        self._authorize("git_reset_to_baseline", caller)
        repo = Repo(self._repo_path)
        self._verify_mandate(mandate, caller=caller, action=ACTION_REVERT, repo=repo)
        if self._reset_runner is not None:
            self._reset_runner(repo, ref)
        else:
            repo.git.reset("--hard", ref)

    def _authorize(self, tool: str, caller: str) -> None:
        if self._policy.check(tool, caller) is not PermissionDecision.ALLOW:
            raise PermissionError(f"{caller} 无权执行 {tool}")

    @staticmethod
    def _active_branch_name(repo: Repo) -> str | None:
        """返回当前分支名；detached HEAD 或不可判定时返回 None。"""
        try:
            return repo.active_branch.name
        except TypeError:
            return None

    def _verify_mandate(
        self,
        mandate: Mandate,
        *,
        caller: str,
        action: str,
        repo: Repo,
    ) -> None:
        """执行前按仓库真实分支再次比对票据；detached HEAD 一律 DENY。"""
        branch = self._active_branch_name(repo)
        if branch is None:
            raise PermissionError("当前仓库处于 detached HEAD，无法比对 Mandate 目标分支")
        if (
            verify_mandate(
                mandate,
                caller=caller,
                action=action,
                target_branch=branch,
            )
            is not PermissionDecision.ALLOW
        ):
            raise PermissionError(f"{caller} 的 Mandate 不匹配 {action}（当前分支 {branch}）")
