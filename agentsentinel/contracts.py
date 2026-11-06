"""领域契约：测试报告、审计记录与任务状态。

字段依据：架构规范 §2.3、§3.3。
"""

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class TestStatus(StrEnum):
    """测试结论；规范 §2.3 只定义 PASS/FAIL 两态。"""

    # 名字以 Test 开头，避免被 pytest 误收集为测试类。
    __test__ = False

    PASS = "PASS"
    FAIL = "FAIL"


class ActionType(StrEnum):
    """审计动作；规范 §3.3 只允许 push/revert。"""

    PUSH = "push"
    REVERT = "revert"


class ScanVerdict(StrEnum):
    """脱敏扫描结论。

    NOT_RUN 表示扫描未执行（权限拒绝/ASK 等场景），调用方不得用 ALLOW 伪装。
    """

    ALLOW = "ALLOW"
    DENY = "DENY"
    NOT_RUN = "NOT_RUN"


class TaskState(StrEnum):
    """任务在流水线中的状态。"""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    PASSED = "passed"
    FAILED = "failed"


class TestReport(BaseModel):
    """测试 Agent 输出到 workspace/reports/test-{timestamp}.json 的报告。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    # 名字以 Test 开头，避免被 pytest 误收集为测试类。
    __test__ = False

    status: TestStatus
    summary: str
    details: str = ""
    timestamp: datetime


class AuditRecord(BaseModel):
    """审计日志中的一条记录；字段与规范 §3.3 一一对应。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    timestamp: datetime
    caller: str
    commit_hash: str
    scan_result: ScanVerdict
    test_result: TestStatus
    action_type: ActionType
    # 权限拒绝固定 PERMISSION_DENIED；扫描拦截为 SCAN_DENIED:<原因>；正常为 ""。
    reason: str = ""


class StateRecord(BaseModel):
    """会话状态流转记录；写入 workspace/reports/pipeline-state.jsonl。

    与 AuditRecord 解耦：审计只保留 push / revert / DENY，状态流转单独成流。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    timestamp: datetime
    from_state: str
    to_state: str
    detail: str = ""


class UsageRecord(BaseModel):
    """一次模型调用的用量记录；写入 workspace/reports/usage.jsonl。

    与审计、状态流解耦：成本只进用量日志，不混入 push-log 或 pipeline-state。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    timestamp: datetime
    run_id: str
    role: str
    tier: str
    model: str
    input_tokens: int
    output_tokens: int
    cache_input_tokens: int = 0
    cache_creation_input_tokens: int = 0
    estimated_cost: float
    cumulative_cost: float
    budget_limit: float
    exceeded: bool = False
    usage_available: bool = True


class TaskStatus(BaseModel):
    """主编排维护的任务状态快照。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    task_id: str
    updated_at: datetime
    state: TaskState = TaskState.PENDING
    detail: str = ""
