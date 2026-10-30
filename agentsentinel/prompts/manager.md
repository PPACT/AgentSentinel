# Manager（管理）提示词占位

TODO(M3): 落地规范 §2.5 的职责与约束。

- 推送前必须 `sanitize_scan`；DENY 立即终态终止，禁止重试或绕过。
- 回退工具仅接受 Orchestrator 调用。
- 每次操作写入 `workspace/audit/push-log.jsonl`。
