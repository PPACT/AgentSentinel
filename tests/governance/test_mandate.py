"""Mandate 票据 MVP：字段比对、Manager 审计、GitOps 分支校验与编排票据。

票据首期是纯 JSON 最小字段集，不做加密/签名；越权一律 DENY 并写审计，
审计 reason 沿用既有 ``PERMISSION_DENIED``，不新增取值。
"""

import json
from pathlib import Path

import pytest
from agentscope.model import ChatModelBase
from git import Repo
from pydantic import ValidationError

from agentsentinel.contracts import ScanVerdict, TestStatus
from agentsentinel.governance import (
    ACTION_PUSH,
    ACTION_REVERT,
    AllowAllSanitizer,
    Mandate,
    MatrixPermissionPolicy,
    PermissionDecision,
    verify_mandate,
)
from agentsentinel.governance.permissions import ORCHESTRATOR_NAME
from agentsentinel.pipeline import SentinelPipeline
from agentsentinel.roles import (
    AgentResult,
    Manager,
    PipelineOutcome,
    Tester,
)
from agentsentinel.tools.git_ops import GitPythonOps, StubGitOps
from agentsentinel.workspace import Workspace

PERMISSION_DENIED = "PERMISSION_DENIED"


def _mandate(
    *,
    caller: str = ORCHESTRATOR_NAME,
    action: str = ACTION_PUSH,
    target_branch: str = "main",
) -> Mandate:
    return Mandate(caller=caller, allowed_action=action, target_branch=target_branch)


def _last_audit(manager: Manager) -> dict[str, object]:
    line = manager.audit_log_path.read_text(encoding="utf-8").splitlines()[-1]
    record: dict[str, object] = json.loads(line)
    return record


def _manager(tmp_path: Path) -> tuple[Manager, StubGitOps]:
    git_ops = StubGitOps()
    manager = Manager(
        workspace=Workspace(tmp_path / "workspace"),
        sanitizer=AllowAllSanitizer(),
        git_ops=git_ops,
        policy=MatrixPermissionPolicy(),
        target_branch="main",
    )
    return manager, git_ops


# --- 纯函数：verify_mandate ------------------------------------------------


def test_verify_mandate_denies_missing_ticket() -> None:
    decision = verify_mandate(
        None,
        caller=ORCHESTRATOR_NAME,
        action=ACTION_PUSH,
        target_branch="main",
    )

    assert decision is PermissionDecision.DENY


def test_verify_mandate_denies_caller_mismatch() -> None:
    decision = verify_mandate(
        _mandate(caller="Coder"),
        caller=ORCHESTRATOR_NAME,
        action=ACTION_PUSH,
        target_branch="main",
    )

    assert decision is PermissionDecision.DENY


def test_verify_mandate_denies_action_mismatch() -> None:
    decision = verify_mandate(
        _mandate(action=ACTION_REVERT),
        caller=ORCHESTRATOR_NAME,
        action=ACTION_PUSH,
        target_branch="main",
    )

    assert decision is PermissionDecision.DENY


def test_verify_mandate_denies_branch_mismatch() -> None:
    decision = verify_mandate(
        _mandate(target_branch="dev"),
        caller=ORCHESTRATOR_NAME,
        action=ACTION_PUSH,
        target_branch="main",
    )

    assert decision is PermissionDecision.DENY


def test_verify_mandate_allows_match_and_skips_empty_branch() -> None:
    mandate = _mandate()

    assert (
        verify_mandate(
            mandate,
            caller=ORCHESTRATOR_NAME,
            action=ACTION_PUSH,
            target_branch="main",
        )
        is PermissionDecision.ALLOW
    )
    assert (
        verify_mandate(
            mandate,
            caller=ORCHESTRATOR_NAME,
            action=ACTION_PUSH,
            target_branch=None,
        )
        is PermissionDecision.ALLOW
    )


def test_mandate_forbids_extra_fields() -> None:
    with pytest.raises(ValidationError):
        Mandate(
            caller=ORCHESTRATOR_NAME,
            allowed_action=ACTION_PUSH,
            target_branch="main",
            extra="x",  # type: ignore[call-arg]
        )


# --- Manager：publish / revert 的票据校验与审计 ---------------------------


@pytest.mark.parametrize(
    "mandate",
    [
        None,
        _mandate(caller="Coder"),
        _mandate(action=ACTION_REVERT),
        _mandate(target_branch="dev"),
    ],
    ids=["missing", "wrong-caller", "wrong-action", "wrong-branch"],
)
def test_publish_denies_invalid_mandate_and_audits(tmp_path: Path, mandate: Mandate | None) -> None:
    manager, git_ops = _manager(tmp_path)

    published = manager.publish(
        caller=ORCHESTRATOR_NAME,
        commit_message="feat: demo",
        mandate=mandate,
        payload="clean content",
        test_status=TestStatus.PASS,
    )

    assert published is False
    assert git_ops.calls == []
    record = _last_audit(manager)
    assert record["action_type"] == "push"
    assert record["caller"] == ORCHESTRATOR_NAME
    assert record["scan_result"] == ScanVerdict.NOT_RUN.value
    assert record["reason"] == PERMISSION_DENIED


def test_publish_with_matching_mandate_reaches_git(tmp_path: Path) -> None:
    manager, git_ops = _manager(tmp_path)

    published = manager.publish(
        caller=ORCHESTRATOR_NAME,
        commit_message="feat: demo",
        mandate=_mandate(),
        payload="clean content",
        test_status=TestStatus.PASS,
    )

    assert published is True
    assert [call[0] for call in git_ops.calls] == ["commit", "tag_baseline", "push"]


@pytest.mark.parametrize(
    "mandate",
    [
        None,
        _mandate(caller="Coder", action=ACTION_REVERT),
        _mandate(action=ACTION_PUSH),
        _mandate(action=ACTION_REVERT, target_branch="dev"),
    ],
    ids=["missing", "wrong-caller", "wrong-action", "wrong-branch"],
)
def test_revert_denies_invalid_mandate_and_audits(tmp_path: Path, mandate: Mandate | None) -> None:
    manager, git_ops = _manager(tmp_path)

    with pytest.raises(PermissionError):
        manager.revert(
            caller=ORCHESTRATOR_NAME,
            ref="baseline-1",
            test_status=TestStatus.FAIL,
            mandate=mandate,
        )

    assert git_ops.calls == []
    record = _last_audit(manager)
    assert record["action_type"] == "revert"
    assert record["scan_result"] == ScanVerdict.NOT_RUN.value
    assert record["reason"] == PERMISSION_DENIED


def test_revert_with_matching_mandate_reaches_git(tmp_path: Path) -> None:
    manager, git_ops = _manager(tmp_path)

    manager.revert(
        caller=ORCHESTRATOR_NAME,
        ref="baseline-1",
        test_status=TestStatus.FAIL,
        mandate=_mandate(action=ACTION_REVERT),
    )

    assert git_ops.calls == [("reset_to_baseline", "baseline-1")]


# --- GitPythonOps：执行前按真实分支再次校验 -------------------------------


def _repo(tmp_path: Path) -> Repo:
    repository = Repo.init(tmp_path)
    with repository.config_writer() as config:
        config.set_value("user", "name", "Test")
        config.set_value("user", "email", "test@example.com")
    return repository


def _git_python_ops(repo: Repo, pushed: list[Repo]) -> GitPythonOps:
    return GitPythonOps(
        Path(repo.working_tree_dir or "."),
        policy=MatrixPermissionPolicy(),
        remote_push=pushed.append,
    )


def test_git_python_push_accepts_matching_branch(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    pushed: list[Repo] = []
    ops = _git_python_ops(repo, pushed)

    ops.push(
        caller=ORCHESTRATOR_NAME,
        mandate=_mandate(target_branch=repo.active_branch.name),
        payload="clean content",
    )

    assert pushed == [repo]


def test_git_python_push_denies_branch_mismatch(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    pushed: list[Repo] = []
    ops = _git_python_ops(repo, pushed)

    with pytest.raises(PermissionError):
        ops.push(
            caller=ORCHESTRATOR_NAME,
            mandate=_mandate(target_branch="not-" + repo.active_branch.name),
            payload="clean content",
        )

    assert pushed == []


def test_git_python_denies_detached_head(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    artifact = tmp_path / "README.txt"
    artifact.write_text("seed\n", encoding="utf-8")
    repo.git.add("--", "README.txt")
    repo.index.commit("initial")
    repo.git.checkout("--detach", "HEAD")
    pushed: list[Repo] = []
    ops = _git_python_ops(repo, pushed)

    with pytest.raises(PermissionError):
        ops.push(caller=ORCHESTRATOR_NAME, mandate=_mandate(), payload="clean content")

    assert pushed == []


def test_git_python_reset_denies_branch_mismatch(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    calls: list[tuple[Repo, str]] = []
    ops = GitPythonOps(
        Path(repo.working_tree_dir or "."),
        policy=MatrixPermissionPolicy(),
        reset_runner=lambda repository, ref: calls.append((repository, ref)),
    )

    with pytest.raises(PermissionError):
        ops.reset_to_baseline(
            "baseline-1",
            caller=ORCHESTRATOR_NAME,
            mandate=_mandate(action=ACTION_REVERT, target_branch="not-" + repo.active_branch.name),
        )

    assert calls == []


# --- 编排层：推送/回退生成正确票据 ---------------------------------------


class _RecordingGitOps(StubGitOps):
    def __init__(self, *, target_branch: str = "main") -> None:
        super().__init__(target_branch=target_branch)
        self.mandates: list[Mandate] = []

    def push(self, *, mandate: Mandate, caller: str = "Manager", payload: str = "") -> None:
        self.mandates.append(mandate)
        super().push(mandate=mandate, caller=caller, payload=payload)

    def reset_to_baseline(
        self, ref: str, *, mandate: Mandate, caller: str = ORCHESTRATOR_NAME
    ) -> None:
        self.mandates.append(mandate)
        super().reset_to_baseline(ref, mandate=mandate, caller=caller)


class _AlwaysFailingTester(Tester):
    def handle(
        self,
        task: str,
        workspace: Workspace,
        *,
        feedback: str = "",
        model: ChatModelBase | None = None,
    ) -> AgentResult:
        return AgentResult(agent=self.name, summary="forced failure", status=TestStatus.FAIL)


def test_pipeline_issues_push_mandate(tmp_path: Path) -> None:
    git_ops = _RecordingGitOps()

    result = SentinelPipeline(Workspace(tmp_path / "workspace"), git_ops=git_ops).run(
        "实现登录接口"
    )

    assert result.status is PipelineOutcome.PASSED
    assert git_ops.mandates == [_mandate()]


def test_pipeline_issues_revert_mandate(tmp_path: Path) -> None:
    git_ops = _RecordingGitOps(target_branch="release")

    result = SentinelPipeline(
        Workspace(tmp_path / "workspace"),
        git_ops=git_ops,
        baseline_ref="baseline-seed",
        target_branch="release",
        tester=_AlwaysFailingTester(),
    ).run("实现登录接口")

    assert result.status is PipelineOutcome.FAILED
    assert git_ops.mandates == [_mandate(action=ACTION_REVERT, target_branch="release")]
