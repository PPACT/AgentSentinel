"""包可导入性与基础元信息的最小冒烟测试。"""

import re

import agentsentinel


def test_package_imports() -> None:
    assert agentsentinel.__version__


def test_version_is_semver() -> None:
    assert re.fullmatch(r"\d+\.\d+\.\d+", agentsentinel.__version__)
