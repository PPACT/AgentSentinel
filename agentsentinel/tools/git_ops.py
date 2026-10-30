"""受控 Git 操作接口与 S1 桩实现。

S1 不触碰真实仓库；GitPython 实现、调用者校验与审计在 M2 落地。
"""

from typing import Protocol


class GitOps(Protocol):
    def commit(self, message: str) -> str: ...

    def push(self) -> None: ...

    def tag_baseline(self, name: str) -> None: ...

    def reset_to_baseline(self, ref: str) -> None: ...


class StubGitOps:
    """只记录调用并返回假 hash，绝不执行 git。TODO(M2): 替换为 GitPython 实现。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self._counter = 0

    def commit(self, message: str) -> str:
        self._counter += 1
        self.calls.append(("commit", message))
        return f"stub{self._counter:07d}"

    def push(self) -> None:
        self.calls.append(("push", ""))

    def tag_baseline(self, name: str) -> None:
        self.calls.append(("tag_baseline", name))

    def reset_to_baseline(self, ref: str) -> None:
        self.calls.append(("reset_to_baseline", ref))
