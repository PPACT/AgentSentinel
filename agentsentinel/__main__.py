"""CLI 入口：离线模型驱动流水线（stub 模型，不联网、不推送真实远端）。"""

import argparse
from collections.abc import Sequence

from agentsentinel.pipeline import SentinelPipeline
from agentsentinel.roles import PipelineOutcome


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="agentsentinel",
        description="AgentSentinel 离线编排演示（脚本模型驱动 AgentScope Agent）",
    )
    parser.add_argument("command", nargs="?", choices=["run"], help="运行离线编排")
    parser.add_argument(
        "--stub",
        action="store_true",
        help="兼容旧参数：使用离线脚本模型跑一次完整流水线",
    )
    parser.add_argument(
        "--task", default="离线演示：实现用户登录接口", help="任务描述，可用换行分号拆解"
    )
    args = parser.parse_args(argv)

    if not args.stub and args.command != "run":
        parser.print_usage()
        return 2

    result = SentinelPipeline().run(args.task)
    print(f"status: {result.status.value}")
    print(f"summary: {result.summary}")
    print("artifacts:")
    for path in result.artifacts:
        print(f"  - {path}")
    for status in result.task_statuses:
        print(f"task {status.task_id}: {status.state.value} ({status.detail})")
    print(f"audit: {result.audit_log}")
    return 0 if result.status is PipelineOutcome.PASSED else 1


if __name__ == "__main__":
    raise SystemExit(main())
