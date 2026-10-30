"""Writer 产物落点与 200 字摘要上限。"""

from pathlib import Path

from agentsentinel.roles import Writer
from agentsentinel.roles.writer import SUMMARY_LIMIT
from agentsentinel.workspace import Workspace


def test_writer_truncates_summary_to_limit(tmp_path: Path) -> None:
    workspace = Workspace(tmp_path / "workspace")
    long_task = "需求" * 300

    result = Writer().handle(long_task, workspace)

    assert len(result.summary) == SUMMARY_LIMIT
    assert result.artifacts == (workspace.docs_dir / "CHANGELOG.md",)
    assert (workspace.docs_dir / "CHANGELOG.md").is_file()


def test_writer_has_no_git_tools() -> None:
    assert not any(tool.startswith("git") for tool in Writer.tools)
