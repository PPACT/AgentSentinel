# 项目 Skills

四个第三方 skill，扁平存放于 `.claude/skills/<name>/SKILL.md`（Claude Code 只识别这一层布局；分类信息记录在此文件中，本文件不会被当作 skill 加载）。

| 分类 | Skill | 用途 |
|---|---|---|
| 01-planning | `brainstorming` | 动手前澄清需求、比较方案、产出设计文档 |
| 01-planning | `writing-plans` | 把需求拆成可执行的实现计划 |
| 02-coding | `typescript-tooling` | TypeScript 工具链：ESLint / Prettier / Vitest / tsconfig / 构建 |
| 03-verification | `verification-before-completion` | 声称"完成/通过"前必须有新跑出来的验证证据 |

## 来源

| Skill | 仓库 | 分支 | Commit |
|---|---|---|---|
| brainstorming | https://github.com/obra/superpowers | `main` | `2ce8aa15d05bd57213e6f746f953aafb7ded7a8b` |
| writing-plans | https://github.com/obra/superpowers | `main` | 同上 |
| verification-before-completion | https://github.com/obra/superpowers | `main` | 同上 |
| typescript-tooling | https://github.com/HoangNguyen0403/agent-skills-standard | `develop` | `3146f69bb7e9f98c09c9968bf2b32ac56cce9ad8` |

两个仓库均为 MIT 许可。导入日期：2026-10-28。

导入方式为按 commit 定点抓取单个文件（未 clone 仓库，项目内无任何 `.git`）。

## 保留与排除

- 保留各 skill 正文（`SKILL.md`）引用到的附属文件：`brainstorming/` 的 `builder-check-prompt.md`、`visual-companion.md`、`scripts/`，以及 `typescript-tooling/references/REFERENCE.md`。
- 排除 `typescript-tooling/evals/evals.json`（上游评测用例，属测试代码）。
- 排除 `.DS_Store`、`.git`、上游仓库的 README / LICENSE / 测试等无关内容。

## 维护

- 新增或修改 skill 后需**重启会话**，`/` 菜单才会刷新。
- 上游更新时按上表的仓库 + 分支重新抓取，并更新本文档中的 commit。
- skill 的 `name` frontmatter 必须与目录名一致（四个均已对齐）。
