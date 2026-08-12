# Agent Runtime 主线任务账本

更新时间：2026-08-12

权威主线：`codex/agent-runtime-ar-17-4-integration`

当前基线：`a6930567`

当前工作区：干净；未推送、未部署、未连接生产。

## 账本规则

分支上的 commit 不等于主线未集成。判断集成必须同时核对：

1. 当前主线祖先链；
2. 当前源码、migration、调用方和测试入口；
3. 与旁路分支的实际 diff。

旁路分支如果只是另一条实现链上的等价提交，不重复 cherry-pick。

## 当前主线已核实状态

| 区域 | 状态 | 证据/边界 |
|---|---|---|
| AR-17.1～17.3 | 已进入当前主线并有本地/CI/PG 合同证据 | 224～226 及 Runtime facts、Executor、reconcile、cancel |
| AR-17.4 基础闭环 | 已完成本地/CI 验收 | Credential、Provider Facts、Artifact、Workspace、Child Run、Scheduler CAS |
| T17.1～T17.7 | 已完成本地/CI 合同批次 | status、observability、kill、operations、recovery、cost、credential boundary |
| C7 S1 模型链 | 已收敛到现有模型选择/配置/adapter 边界 | 独立 Gateway 仅保留历史 migration/测试证据，当前 Python Owner 不依赖它 |
| C7 B3.1/B3.2 | 已完成 | 生产 Composition 保持 failure-closed；v6 数据读取发布为 227_58 |
| S2 ERP Read | 已接入 | 复用现有租户 ERP Bundle/Dispatcher；ERP Write、Sync、淘宝奇门关闭 |
| S2 数据读取 | 已接入 | `local_data`、`file_analyze`、`fetch_all_pages` 显式 adapter；无隐式 Artifact fallback |
| AR-18 cancel | 技术批次已在主线形成等价集成 | Run/Action/Provider/Model/Sandbox/Child cancel 及 fence 有测试证据 |
| AR-18 Scheduler/WeCom | 技术批次已大部进入主线 | scheduled Runtime、WeCom delivery/readback/reconcile/worker/CI 需做总复审 |
| Production readiness | 未开启 | `production_ready=false`，生产 flags 默认关闭 |
| T5 Staging | 未执行 | 仅有 disposable local/CI verified，不冒充 staging |
| T8/T9 | 未执行 | 需要真实生产授权和观察窗口，当前不做 |

## 现有 worktree 归类

### 历史或已由等价提交覆盖

- `ar-00`～`ar-16`、`ar-17-1-core`、`ar-17-2-read-executors`、`ar-17-3-specialists`：历史实现来源，当前主线已有对应集成证据。
- `c7-b1-*`、`c7-b2-*`、`c7-b31-*`、`c7-bg*`、`c7-d0-*`、`c7-final-*`：必须以当前源码和实际 diff 复审；不能仅因旁路 tip 不在祖先链就重复合并。当前主线已包含 flags-off、composition、gateway fence、release gate 的等价提交。
- `agent-runtime-final-integration`：其 orphan sandbox recovery 等价提交已在当前主线祖先链中。

### AR-18 已形成等价主线但仍需总复审

- `ar18-a11-*`：lifecycle fence。
- `ar18-a12-*`：cancel intent、Web、Provider、Model、Sandbox、Child Run。
- `ar18-b7-s1-*`、`ar18-b7-s2-*`：Scheduler、scheduled Runtime、Web/WeCom delivery、readback、reconcile、worker、CI。

注意：例如 Sandbox B5 协作分支使用 `929ec2e2`，主线已有等价修复 `77585d21`；应审查实际 diff，不直接 cherry-pick。

## 当前未完成任务

| ID | 任务 | 状态 | 依赖 | 下一动作 |
|---|---|---|---|---|
| S3-R | Scheduler/WeCom 主线总复审 | 待执行 | C7 B3.2 | 核对唯一 Owner、事实、readback/reconcile、旧发送入口 |
| S4-A | Ingress/Owner 静态调用图 | 待执行 | S3-R | 列出 Web、WeCom、Scheduler、Conversation Actor、ToolLoop 全部真实入口 |
| S4-B | 本地 Owner 收敛修复 | 待执行 | S4-A | 仅关闭真实旁路副作用入口；保留兼容投影和 reconcile |
| S4-C | Owner cutover disposable/dry-run | 待执行 | S4-B | crash、drain、fence、duplicate side-effect、UNKNOWN/reconcile |
| S5 | 最终 migration/install manifest | 待执行 | S4-C | 从生产最高 migration 220 与当前主线生成最终安装清单；不执行生产 |
| S6 | AR-17/18 本地与 CI 发布候选验收 | 待执行 | S5 | 全链回归、权限、回滚、单 Owner 证据 |
| T8 | 生产观察期 | 未授权/未执行 | S6 + 明确生产授权 | 不在当前开发批次自动执行 |
| T9 | 旧链清理 | 未授权/未执行 | T8 通过 | 观察期前不得删除兼容代码 |
| T10～T16 | MCP、Skill、Goal、Subagent、企业共享、推荐配置 | 未开始/独立范围 | AR-18 稳定后 | 分别设计，不混入当前接线批次 |
| T17 | 统一运维中心 | 基础批次完成，最终平台验收未完成 | T10～T16 | 作为平台级最终验收的一部分收口 |
| T18 | 完整 Agent 平台生产验收 | 未开始 | T10～T17、T8/T9 | 最后执行 |

## 当前唯一关键路径

```text
C7-B3.2
  → S3-R Scheduler/WeCom 总复审
  → S4-A 旧 Owner/Ingress 调用图
  → S4-B 本地 Owner 收敛
  → S4-C disposable/dry-run
  → S5 最终 migration/install manifest
  → S6 AR-17/18 本地与 CI 发布候选
  → 用户另行授权后 T8 生产观察
  → T9 旧链清理
```

## 不得误判的边界

- 本地/CI 通过不等于 staging 或 production ready。
- 生产未安装 227_02 以后新增 Runtime migration；生产部署必须先生成从生产 migration 220 起的最终 manifest。
- 227_02、227_54～227_57 不能被重写；数据读取 v6 使用 additive `227_58`。
- `ProductionServiceBundle`、tenant binding、Provider readiness 等历史结构暂不能删除，必须先证明无真实调用方和无生产事实引用。
- `UNKNOWN/ACCEPTED` 永远只能 readback/reconcile，不得用 Owner 收敛名义普通重派。
- 未经用户明确生产授权，不推送、不部署、不连接生产、不读取真实 Secret、不调用真实 Provider。

## 每批交付门禁

- 独立 worktree、仅任务文件、工作区干净；
- migration identity、rollback、RLS/FORCE RLS、ACL、固定 `search_path`；
- tenant/run/action/attempt/request hash/execution token/revision fence；
- unit、compile、diff check、quality gate；
- 需要数据库时 disposable PostgreSQL apply/readback/rollback/reapply；
- 明确 commit/parent、文件、日志、未完成项和生产边界。
