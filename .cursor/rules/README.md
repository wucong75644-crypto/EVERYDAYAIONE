# 阶段工作流使用指南（Claude 对齐版）

> **更新日期**：2026-07-09
> **版本**：V2.0（与 Claude Code 配置同步）

本目录已与 `~/.claude/` 对齐：底层规则、阶段 skill、commands、hooks、project memory 均以 Claude 为真源回写。

---

## 文件结构

```
EVERYDAYAIONE/
├── AGENTS.md / .cursorrules          # 底层核心规则（V3.3，与 ~/.claude/CLAUDE.md 对齐）
└── .cursor/
    ├── hooks.json                    # 从 Claude hooks 移植
    ├── hooks/                        # hook 脚本
    ├── memory/                       # Claude project memory 副本
    ├── rules/
    │   ├── 1-requirement.md … 5-testing.md
    │   ├── git-workflow.md           # 来自 ~/.claude/rules/
    │   ├── patterns.md               # 来自 ~/.claude/rules/
    │   ├── project-memory.mdc        # alwaysApply memory
    │   └── README.md
    └── skills/
        ├── requirement-analysis/     # ← everydayai-requirement
        ├── ui-design/                # ← everydayai-ui-design
        ├── dev-doc/                  # ← everydayai-tech-design
        ├── implementation/           # ← everydayai-implementation
        ├── testing-bugfix/          # ← everydayai-testing
        ├── everydayai-deploy/
        ├── everydayai-evaluate/
        ├── everydayai-git-push/
        ├── everydayai-restart/
        ├── everydayai-review/
        └── everydayai-test-coverage/
```

用户级同步位置：
- `~/.agents/skills/source-command-everydayai-*`
- `~/.cursor/skills/everydayai-*/SKILL.md`

---

## 使用方式

### 自动触发（推荐）
AI 根据描述自动加载对应 skill：
- "我想要…""实现一个…" → requirement
- "界面设计""页面布局" → ui-design
- "技术方案""数据库设计" → tech-design / evaluate
- "开始开发""写代码" → implementation
- "有 bug""报错" → testing
- "检查测试""测试覆盖" → test-coverage
- 代码修改、新建、拆分或 Bug 修复完成 → test-coverage
- "评审方案""讨论一下" → evaluate
- "审查""review" → review
- "部署""上线" → deploy
- "推送代码""push" → git-push
- "重启服务" → restart

### 手动引用
- `@1-requirement` / `@requirement-analysis`
- `@2-ui-design` / `@ui-design`
- `@3-dev-doc` / `@dev-doc`
- `@4-implementation` / `@implementation`
- `@5-testing` / `@testing-bugfix`

---

## Hooks（与 Claude 行为对齐）

| 事件 | 行为 |
|------|------|
| beforeShellExecution | 拦截直接 `npm/pnpm/yarn/bun run dev`，要求 tmux |
| beforeShellExecution | 长命令（install/test 等）提示使用 tmux |
| beforeShellExecution | `git push` 前提醒 review |
| afterShellExecution | `gh pr create` 后输出 PR URL |
| afterFileEdit | TS/JS 编辑后 prettier；tsc 报错提示；`console.log` 警告 |

---

## 规则优先级

1. `AGENTS.md` / `.cursorrules`（底层，最高）
2. `project-memory.mdc`（项目记忆，alwaysApply）
3. 阶段 skills / rules（按需）
4. hooks（运行时拦截与后处理）

冲突时：底层规则 > 阶段规则。

---

## 同步说明

- **真源**：`~/.claude/CLAUDE.md` + `~/.claude/commands/` + `~/.claude/rules/` + Claude project memory
- **回写目标**：本仓库 `.cursor/`、`AGENTS.md`、`.cursorrules`，以及 `~/.agents/skills`、`~/.cursor/skills`、`~/AGENTS.md`
- **测试 Skill 真源**：`.cursor/skills/everydayai-test-coverage/SKILL.md`；`.claude/skills/` 使用轻量转发，避免规则漂移
- 小改动（改颜色/文案/单文件小 bug）无需走完整阶段流程

---

**维护者**：技术团队  
**最后同步**：2026-07-09（Claude → Cursor）
