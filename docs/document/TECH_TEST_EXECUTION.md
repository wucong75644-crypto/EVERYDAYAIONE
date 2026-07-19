# 测试执行分层与 AI Token 控制

> 状态：已实施
> 日期：2026-07-19

## 目标

- 日常开发优先获得 5–30 秒反馈。
- 测试成功时不向 AI 上下文写入逐条用例日志。
- 真实数据库、网络和第三方服务必须显式授权。
- 提速不能以删除、跳过或隐藏测试为代价。

## 分层

| 层级 | 边界 | 默认时机 |
|------|------|----------|
| Small | 无网络、真实数据库、真实等待 | 日常开发 |
| Medium | 本地文件、进程或多模块协作 | PR |
| Large | E2E、大文件、性能、并发、长时间 | 夜间/发布前 |
| External | 真实数据库、网络、AI API、第三方服务 | 显式授权 |

## 标准命令

```bash
scripts/run_tests.sh target backend/tests/test_xxx.py
scripts/run_tests.sh fast
scripts/run_tests.sh pr
scripts/run_tests.sh full
scripts/run_tests.sh large
RUN_EXTERNAL_TESTS=1 scripts/run_tests.sh external
```

普通开发遵循 `target → 受影响模块 → fast`。A级任务最终验收运行 `pr`；
`full/large/external` 仅在方案、发布门禁或用户要求时运行。

## AI 渐进加载

```text
AGENTS.md 路由大纲
  → everydayai-test-coverage/SKILL.md
    → scripts/run_tests.sh
```

`AGENTS.md` 不复制详细测试流程。Cursor Skill 是项目内测试规则真源；
Claude Skill 只转发到该真源，避免双份规则漂移。

## 输出合同

- 默认 `-q --tb=short`，首次失败 `--maxfail=1`。
- 成功只报告命令、数量、耗时和覆盖率。
- 失败只报告首个有效短堆栈；完整日志保留在本地。
- 慢测试最多报告 10 条。
- 覆盖率默认只计算受影响模块。

## 准确性门禁

- marker 只能改变运行时机，不能降低测试可发现总数。
- Small 测试必须隔离网络、真实数据库和第三方服务。
- External 必须同时满足 marker、显式环境开关和用户授权。
- 引入并行执行前必须验证共享状态、端口、临时目录和顺序独立性。
- 既有失败必须记录，禁止通过新增 xfail 或 deselect 伪装通过。
