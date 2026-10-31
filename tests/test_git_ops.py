"""受控 Git 工具：真实临时仓库 + 模拟远端/回退，校验不可绕过。"""

from pathlib import Path

import pytest
from git import Repo

from agentsentinel.governance.permissions import MatrixPermissionPolicy
from agentsentinel.governance.sanitizer import RegexSanitizer
from agentsentinel.tools.git_ops import GitPythonOps, ScanDeniedError


@pytest.fixture
def repo(tmp_path: Path) -> Repo:
    repository = Repo.init(tmp_path)
    with repository.config_writer() as config:
        config.set_value("user", "name", "Test")
        config.set_value("user", "email", "test@example.com")
    artifact = tmp_path / "workspace" / "code" / "main.py"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("def main() -> str:\n    return 'ok'\n", encoding="utf-8")
    return repository


def _ops(
    repo: Repo,
    *,
    remote_push: object = None,
    reset_runner: object = None,
) -> GitPythonOps:
    return GitPythonOps(
        Path(repo.working_tree_dir or "."),
        policy=MatrixPermissionPolicy(),
        sanitizer=RegexSanitizer(),
        remote_push=remote_push,  # type: ignore[arg-type]
        reset_runner=reset_runner,  # type: ignore[arg-type]
    )


def test_commit_records_hash_for_orchestrator(repo: Repo, tmp_path: Path) -> None:
    ops = _ops(repo)

    commit_hash = ops.commit("feat: demo", caller="Orchestrator")

    assert len(commit_hash) == 40
    assert repo.head.commit.hexsha == commit_hash


def test_commit_by_manager_requires_approval(repo: Repo) -> None:
    ops = _ops(repo)

    with pytest.raises(PermissionError):
        ops.commit("feat: demo", caller="Manager")

    assert not repo.head.is_valid()


def test_tag_baseline(repo: Repo) -> None:
    ops = _ops(repo)
    commit_hash = ops.commit("feat: demo", caller="Orchestrator")

    ops.tag_baseline("baseline-1", caller="Orchestrator")

    assert repo.tags["baseline-1"].commit.hexsha == commit_hash


def test_push_uses_mocked_remote(repo: Repo) -> None:
    pushed: list[Repo] = []
    ops = _ops(repo, remote_push=pushed.append)
    ops.commit("feat: demo", caller="Orchestrator")

    ops.push(caller="Orchestrator", payload="clean content")

    assert pushed == [repo]


def test_push_with_secret_is_blocked_before_remote(repo: Repo) -> None:
    pushed: list[Repo] = []
    ops = _ops(repo, remote_push=pushed.append)

    with pytest.raises(ScanDeniedError):
        ops.push(caller="Orchestrator", payload='password = "hunter2"')

    assert pushed == []


def test_reset_rejects_non_orchestrator_and_skips_runner(repo: Repo) -> None:
    calls: list[tuple[Repo, str]] = []
    ops = _ops(repo, reset_runner=lambda repository, ref: calls.append((repository, ref)))

    with pytest.raises(PermissionError):
        ops.reset_to_baseline("HEAD", caller="Manager")

    assert calls == []


def test_reset_uses_injected_runner_for_orchestrator(repo: Repo) -> None:
    calls: list[tuple[Repo, str]] = []
    ops = _ops(repo, reset_runner=lambda repository, ref: calls.append((repository, ref)))

    ops.reset_to_baseline("baseline-1", caller="Orchestrator")

    assert calls == [(repo, "baseline-1")]


def test_push_denies_when_scan_finds_secret_regardless_of_caller(repo: Repo) -> None:
    ops = _ops(repo, remote_push=lambda _: None)

    with pytest.raises(ScanDeniedError):
        ops.push(caller="Orchestrator", payload="AKIAIOSFODNN7EXAMPLE")
