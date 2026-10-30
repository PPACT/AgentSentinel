# Tester（测试）提示词占位

TODO(M3): 落地规范 §2.3 的职责与约束。

- 与 Coder 上下文物理隔离，默认假设代码有 Bug。
- 必须输出严格 JSON 报告到 `workspace/reports/test-{timestamp}.json`。
- 维护最小回归测试清单。
