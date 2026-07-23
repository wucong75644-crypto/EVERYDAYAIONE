# Agent Runtime 测试、灰度发布与回滚设计

> 状态：总体设计 / 第一轮冻结
> 日期：2026-07-18
> 范围：正确性、模型质量、真实依赖、Trace、发布证据、Canary 和回滚

## 1. 结论

Agent Runtime 的测试对象是跨模型、工具、数据库、Provider、回调、成本和 UI 的长期状态机：

```text
Pure/Schema
 -> State Machine/Contract
 -> Real Dependency Integration
 -> Deterministic Trace Replay
 -> Channel E2E
 -> Eval/Chaos/Load
 -> Release Evidence
 -> Canary/Reconciliation/Rollback
```

冻结原则：

1. 确定性正确性与 LLM 质量评测分开。
2. 所有状态转移可用固定时钟、ID、随机源和 Provider 响应重放。
3. PostgreSQL、Redis、对象存储、Callback 至少有真实依赖契约测试。
4. 测试失败必须阻断发布，不能 warning 后继续。
5. 迁移采用 expand → compatible code → backfill → shadow → switch → contract。
6. Canary 按 org/user/channel/action/release 控制。
7. 回滚应用版本不重放 Accepted/Unknown 外部动作，只进入 reconciliation。
8. 没有 Release Evidence 不切全量。

## 2. 当前基础与风险

已有：

- 后端大量 pytest、前端 Vitest/Zod。
- 前端全局 coverage 80%。
- Actor enqueue/claim/lease/fencing/terminal/Outbox 测试。
- 119–135 连续 rollback SQL。
- 图片 Provider retry、任务恢复、消息 optimistic rollback。
- Web/企微 Sink 和附件状态覆盖。

风险：

- 多数迁移测试只断言 SQL 文本，不能证明真实 PostgreSQL 并发。
- 大量 patch/mock 分段证明，缺少全链 Trace。
- `deploy/deploy.sh` 测试失败后继续部署。
- 缺不可变 release artifact、schema compatibility、Actor drain 和 org canary。
- `/health` 不足以表示 Runtime/Worker/Outbox/Provider readiness。
- 缺重复扣费、Unknown、Artifact 缺失和 UI terminal convergence 自动门禁。

## 3. 测试分层

| 层 | 对象 | 门禁 |
|---|---|---|
| Pure | reducer、resolver、budget、Policy | PR |
| Schema | Action/Event/Result/ContentPart/Manifest | PR |
| State machine | Run/Action/Goal/Interaction/Delivery | PR |
| Contract | Tool/MCP/Provider/DB RPC/Channel | PR |
| Integration | 真 PostgreSQL/Redis/MinIO/Fake HTTP | PR/受限 |
| Trace Replay | 全 Runtime 事件 | PR 核心集、nightly 全集 |
| Channel E2E | Web/企微 simulator | PR 核心集 |
| Eval | 意图、规划、Skill、克制率 | nightly/release |
| Chaos/Load | crash、乱序、积压、容量 | nightly/release |

Policy、状态机、协议模块分支覆盖目标 `>=90%`；全局保持 `>=80%`。覆盖率不能替代边界和竞态测试。

## 4. Runtime Trace Bundle

```json
{
  "schema_version": 1,
  "clock_seed": "2026-07-18T00:00:00Z",
  "id_seed": 42,
  "release_manifest": {},
  "config_snapshot": {},
  "catalog_revisions": {},
  "inputs": [],
  "model_responses": [],
  "tool_outputs": [],
  "provider_callbacks": [],
  "commands": [],
  "expected_events": [],
  "expected_state": {},
  "expected_projection": {}
}
```

固定 wall clock、UUID、退避随机、模型输出和 Provider。大文件用 Artifact fixture hash/URI，禁止真实 Secret 和用户敏感正文。

比较：

- 完全相等：状态、sequence、cost ledger、terminal、artifact lineage。
- 规范化相等：时间、临时路径、provider request ID。
- 约束相等：自然语言、活动描述。

Fixture manifest 与磁盘集合做 set equality，防止测试样例静默丢失。

## 5. 核心 Trace

首批必须覆盖：

1. 普通无工具聊天。
2. 用户只写提示词，不执行。
3. 明确单图生成。
4. 三段提示词拆三 Action，部分失败。
5. 图片 submit timeout → Unknown → callback 成功。
6. 视频等待期间继续聊天。
7. ERP 查询与数据范围。
8. ERP 写入响应丢失，不重复提交。
9. 文件分析 → Artifact → 图表。
10. Skill Instruction。
11. Workflow SkillRun 批量执行。
12. MCP 只读、OAuth 过期和 schema 漂移。
13. SubRun 并行完成与 parent wake。
14. Interaction 断线、多端 first answer。
15. Web gap recovery。
16. 企微富内容降级。
17. Goal pause/resume/budget exhausted。
18. Worker crash/release rollback/reconcile。

## 6. 故障注入

| Crash point | 预期 |
|---|---|
| claim 后执行前 | lease 到期重领 |
| cost reserve 后 submit 前 | 安全释放 |
| Provider 接受后响应前 | Unknown，不盲重试 |
| TaskRef 落库后 Outbox 前 | 同事务恢复 |
| callback 先于 accepted | Inbox 关联 |
| Artifact 上传后 DB 前 | checksum 对账，不重复上传 |
| DB terminal 后 WS 前 | Snapshot/Replay |
| 企微发送后 checkpoint 前 | at-least-once + 审计 |
| lease renew 丢失 | 旧 owner 停止，fencing |
| Worker SIGTERM | 停止 claim、有界 drain |
| Redis/WS 中断 | DB 事实不丢 |
| 回滚期间 callback | 按 Action schema revision 路由 |

故障工具必须支持 timeout、连接中断、迟到、重复、乱序，不只抛异常。

## 7. Fake 与真实依赖

共享测试服务：

- Fake Model Server：流式 delta、ToolCall、空响应、截断、429、timeout。
- Fake Provider：accept/query/cancel/callback、Unknown、重复 callback。
- Fake MCP Server：catalog drift、OAuth、Tool/Resource/Prompt。
- Channel Simulator：WebSocket reconnect/gap、企微 ACK/expiry/duplicate。
- Virtual Clock/ID/Random。

PR 真实依赖：

- PostgreSQL 与生产 major version 一致。
- Redis。
- MinIO 或兼容 S3。
- HTTP callback receiver。

不允许只通过 SQLite 或 in-memory mock 证明 PostgreSQL 锁和 RPC 正确。

## 8. 数据库迁移测试

每个 migration：

- up/down SQL checksum。
- 空库 up。
- N-1 生产形状样本 upgrade。
- N/N-1 应用兼容。
- RPC 权限和 OrgScopedDB 包装。
- 并发 claim/answer/cancel/callback/materialize。
- 锁范围、执行时长和磁盘增长。
- backfill checkpoint、速率、可重入。

有数据丢失风险的 contract migration 不宣称 down SQL 可恢复数据；必须依赖备份或正向修复。

## 9. CI

```text
PR
 ├─ lint/type/file-size/forbidden-any
 ├─ unit/schema/state-machine
 ├─ frontend coverage
 ├─ PostgreSQL/Redis contract
 ├─ migration up/down/N-1
 └─ core Trace + channel E2E

main/nightly
 ├─ full Trace
 ├─ chaos/load
 ├─ provider smoke with cost cap
 └─ LLM Eval regression
```

规则：

- 任一确定性门禁失败，禁止 merge/deploy。
- 失败测试不自动重试；确认 flaky 后隔离并设置 owner、到期日。
- Provider smoke 使用专用低额度账户和硬成本上限。
- Core Trace 每 PR，长时 chaos/load nightly。
- CI 输出机器可读 Release Evidence。

## 10. LLM Eval

Eval 独立验证：

- 意图和 Tool 选择；
- 多 Prompt 拆 Action；
- 不该执行时的克制率；
- Skill 遵循；
- Goal 规划/Verifier；
- 最终答案引用和数据准确性。

每项记录 model、temperature、prompt revision、Tool/Skill catalog、judge revision 和 seed。Eval 波动不掩盖确定性错误；是否阻断发布由单独产品阈值决定。

首期 release 阈值建议：

- 明确执行意图识别 `>=98%`。
- 非执行提示词场景误触发 `<=0.5%`。
- 多 Prompt 数量/顺序正确 `>=95%`。
- 高风险无授权执行 `0`。
- 引用型数据答案 grounded `>=98%`。

## 11. ReleaseManifest

```json
{
  "release_id": "uuid",
  "git_sha": "...",
  "build_digest": "sha256:...",
  "database_schema_revision": 150,
  "runtime_event_version": 1,
  "action_schema_version": 1,
  "tool_catalog_revision": 7,
  "plugin_catalog_revision": 2,
  "skill_catalog_revision": 3,
  "model_prompt_catalog_revision": 11,
  "config_catalog_revision": 5,
  "migration_checksums": {},
  "test_evidence_uri": "artifact:uuid",
  "eval_evidence_uri": "artifact:uuid"
}
```

Run 固定 release ID，便于重放、callback 路由和事故解释。构建产物不可变，生产部署校验 digest，不直接依赖可变工作目录。

## 12. 发布流程

```text
build immutable artifact
-> verify ReleaseManifest
-> expand migration
-> deploy N/N-1 compatible code
-> shadow write/read
-> canary
-> progressive rollout
-> observe rollback window
-> contract migration
```

Actor Worker：

1. 打开 claim gate，只阻止新 claim，不拒绝 enqueue。
2. 等待 in-flight 排空至 0 或 drain timeout。
3. 未完成任务保持 DB lease，不写失败。
4. 部署兼容 Worker，再部署 API/UI。
5. Callback/Outbox 可接收，必要时暂停消费但不丢记录。
6. readiness 后逐步恢复 claim。

首期 drain timeout 60 秒；长 Action 不要求在进程内完成，释放 lease 后新 Worker 恢复。

## 13. Readiness

Readiness 必查：

- DB 可读写和 schema revision compatible。
- Actor/Action Worker claim gate 状态。
- Redis 可用性（降级能力明确）。
- RuntimeEvent Outbox oldest age/backlog。
- Callback Inbox backlog。
- stuck Run/Action age。
- Cost reservation reconciliation。
- Artifact store 写/读 smoke。
- Extension/MCP Gateway 状态。
- 当前 release/config/catalog revision。

Liveness 只证明进程活着；readiness 不健康必须非 2xx，不接新流量。

## 14. Canary

Flag 维度：

- org ID；
- user cohort；
- channel；
- Action type；
- model/provider；
- release revision。

Run 创建时冻结 routing revision，同 Run 不跨新旧 Runtime。

首期流量：

```text
internal org
-> 1% eligible org
-> 5%
-> 25%
-> 50%
-> 100%
```

每阶段至少覆盖 100 个 Run 或 30 分钟，取更晚者；低流量 Action 用固定最小样本，不只依赖百分比。

## 15. 自动门禁与回滚

相对旧链或前一稳定版本：

| 指标 | 自动暂停/回切阈值 |
|---|---:|
| runtime fatal/error rate | `>2%` 或高 1 个百分点 |
| terminal convergence | `<99.5%` |
| Unknown Action | `>0.5%`，高风险任一异常立即停 |
| duplicate side effect | `>0` |
| cost settlement mismatch | `>0.1%` 或任一负余额 |
| event replay/gap failure | `>0.1%` |
| Artifact missing after completed | `>0.1%` |
| stuck age | 超类型 SLA 的 `>0.5%` |
| Interaction answer failure | `>1%` |

回滚顺序：

1. 关闭新 Runtime claim/route，保留 enqueue。
2. 新 Run 回旧入口。
3. 新 Runtime 已 Accepted/Unknown Action 交给兼容 Reconciler。
4. 回滚应用版本；additive schema 保留。
5. 回放 Outbox/Callback，完成 Artifact/Settlement。
6. 只有验证无新版本引用后才考虑 schema down。

绝不因回滚重新提交外部 Action。

## 16. Shadow 与双写对账

Shadow 阶段：

- 旧链是执行 owner，新 Runtime 只计算 Plan/Policy/Projection。
- 比较 Tool selection、Action args、Policy decision、预计成本和 ContentPart。
- Shadow 不调用付费/外部副作用 Executor。

双写阶段：

- 一个 terminal owner。
- 旧 task 与新 Action 通过 mapping 关联。
- RuntimeEvent/Artifact/Cost 双写同事务或 Outbox。
- 对账器输出 missing/extra/mismatch/lag。

切换门禁：

- 连续 7 天或足够样本无 duplicate side effect。
- terminal/state/cost/artifact mismatch 在阈值内。
- 回滚演练成功。
- Accepted/Unknown 能由新旧兼容 Reconciler 接管。

## 17. 边界场景

| 场景 | 处理 |
|---|---|
| Canary 会话跨版本 | Run 固定 release/routing |
| rollback 后新字段存在 | 旧代码忽略 additive 字段 |
| migration 回填与在线写 | 幂等双写 + checkpoint |
| callback 跨版本 | 按 Action schema revision |
| Provider 无 query | 等 callback/人工，不重试 |
| Eval 下降、正确性绿 | 产品门槛独立决策 |
| Fixture 含敏感信息 | schema-aware redaction + secret scan |
| drain 超时 | lease 恢复，不写业务失败 |
| UI 新协议故障 | flag 回旧 WS adapter |
| Outbox backlog | 停新 claim，先排积压 |

## 18. 计划范围与验收

计划新增：

- `backend/tests/runtime/state_machines/`
- `backend/tests/runtime/contracts/`
- `backend/tests/runtime/traces/`
- `backend/tests/runtime/fakes/`
- `frontend/src/runtime/__tests__/`
- CI workflow、ReleaseManifest builder、canary controller、readiness endpoints
- migration integration harness、channel simulators

迁移：

1. 先修发布测试失败仍继续的问题。
2. 建 schema/state/Trace 基础。
3. 加真 PostgreSQL/Redis 门禁。
4. 加 Projection reducer 与 Web/企微模拟器。
5. 加 ReleaseManifest、readiness、Actor drain。
6. shadow → internal → canary。
7. 演练 kill switch 和 Accepted/Unknown reconciliation。
8. 达到门禁后逐工具切流。

验收：

- CI 失败不能发布。
- 所有核心状态机有非法转移和竞态测试。
- 关键 Trace 可确定性重放。
- 真实数据库证明 CAS/锁/租户边界。
- 任意 crash point 不重复扣费/副作用。
- 断线和回滚后 UI/Artifact/Settlement 收敛。
- ReleaseManifest 可定位每个 Run。
- canary 自动暂停并可回切。
- 旧链退出前完成完整对账和回滚演练。
