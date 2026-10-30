"""CLI 入口：S1 仅提供离线桩演示。"""

import argparse
from collections.abc import Sequence

from agentsentinel.agents import PipelineOutcome
from agentsentinel.pipeline import StubPipeline


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="agentsentinel",
        description="AgentSentinel 离线桩演示（S1）",
    )
    parser.add_argument(
        "--stub",
        action="store_true",
        help="使用确定性桩跑一次离线流水线（S1 唯一可用模式）",
    )
    parser.add_argument("--task", default="离线桩演示：用户登录接口", help="演示任务描述")
    args = parser.parse_args(argv)

    if not args.stub:
        parser.print_usage()
        return 2

    result = StubPipeline().run(args.task)
    print(f"status: {result.status.value}")
    print(f"summary: {result.summary}")
    print("artifacts:")
    for path in result.artifacts:
        print(f"  - {path}")
    print(f"audit: {result.audit_log}")
    return 0 if result.status is PipelineOutcome.PASSED else 1


if __name__ == "__main__":
    raise SystemExit(main())
