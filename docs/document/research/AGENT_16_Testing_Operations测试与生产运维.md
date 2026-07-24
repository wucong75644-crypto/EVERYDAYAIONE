# AGENT 16：Testing / Operations 测试与生产运维

> 状态：第一轮源码对标完成
> 日期：2026-07-18
> Grok Build 基线提交：`c68e39f60462f28d9be5e683d9cbe2c57b1a5027`
> 研究边界：测试分层、确定性回放、故障注入、CI 门禁、迁移、灰度、发布、回滚和生产验收
> 证据限制：Grok 开源仓库没有公开 `.github/workflows`；本文只评价可见源码，不推断其内部 CI/CD

## 1. 结论摘要

Agent 的测试对象不是一个 HTTP 接口，而是一条跨模型、工具、数据库、Provider、
回调和 UI 的长期状态机。目标体系应同时覆盖：

```text
Pure / Schema
  ↓
State-machine / Contract
  ↓
Real-dependency Integration
  ↓
Deterministic Trace Replay
  ↓
Channel E2E
  ↓
Eval / Chaos / Load
  ↓
Release Evidence + Canary + Rollback
```

EVERYDAYAIONE 当前已有大量单元测试和较强的 Actor SQL 契约测试，但发布脚本允许测试
失败后继续部署，迁移多数靠文本断言，缺少统一 Runtime Trace 回放、真实依赖故障矩阵、
按组织灰度和自动回滚门槛。这些问题在普通 CRUD 中尚可人工兜底，在自主执行 Agent
中会直接放大为重复扣费、重复外部动作、丢失终态或无法解释的恢复行为。

推荐原则：

1. 正确性测试与模型质量评测分离，不能用 LLM Eval 代替状态机测试。
2. 所有状态转移必须可在固定时钟、固定 ID、固定随机源下重放。
3. 数据库、Redis、对象存储和回调至少有一层真实依赖集成测试。
4. 发布采用 expand → 双写/回填 → shadow read → canary → switch → contract。
5. 新 Agent Runtime 未形成可审计 Release Evidence 前，不切换全量生产流量。

## 2. Grok Build 可见测试实现

### 2.1 测试资产形态

源码中可见：

- `xai-grok-shell/src/session/acp_session_tests` 有 53 个 Rust 测试文件。
- `xai-grok-shell/tests` 有 33 个集成测试文件。
- `xai-grok-pager/tests` 有 230 个测试相关文件。
- 大量模块在源文件内使用 `#[cfg(test)]` 固定局部不变量。
- `xai-grok-test-support` 提供 Mock inference server 和测试支撑。
- built-binary、stdio leader、多客户端、ACP、PTY 和配置隔离均有集成级测试。

这些数字只能说明仓库结构，不能与 Python/TypeScript 文件数直接比较覆盖质量。

### 2.2 数据驱动 Trace Replay

`xai-grok-shell/tests/trace_replay.rs` 是明确的确定性回放样例：

```text
synthetic_*.json
  → 按 Turn 读取
  → assistant end-of-turn snapshot
  → evaluate_todo_gate
  → 校验 decision / reason / reminder
```

它有两个值得复用的细节：

- Fixture 使用闭合 enum 反序列化，拼写错误直接失败。
- `CANONICAL_FIXTURES` 与磁盘集合做 set equality；Fixture 丢失或偷偷新增都会失败。

但它只回放 TodoGate 的纯函数输入，不是完整 Session、模型和工具的全链路事件回放。
因此我们的目标应扩展为 RuntimeEvent/ModelResponse/ToolOutput/Provider callback 的统一
Trace Bundle。

### 2.3 Session Actor 与 Replay Buffer

`acp_session_tests` 构造完整 `SessionActor` fixture，显式注入：

- Mock filesystem 和 Dummy terminal。
- permission handle。
- event/persistence channel。
- buffering settings：`max_items=100`、`max_bytes=1_000_000`、
  `max_duration_ms=50`。
- inference idle timeout `300s`、`max_retries=3`。
- compaction threshold `85%`。
- Goal、MCP、Hook、Plugin、feedback 和 observability 状态。

Replay Buffer 测试会同时观察 UI notification 和 persistence message，说明 Grok 将
“给客户端发送”和“交给持久化”的双出口作为同一 fixture 的可验证契约。

### 2.4 状态机和竞态测试

可见源码对以下边界有直接测试：

- Goal pause/resume、backoff、blocked、complete 和 budget-limited。
- continuation 幂等抑制、classifier/strategist 并发结果和取消。
- 多个 client 共享 leader、请求 ID 路由、cancel metadata 隔离。
- Session resume 后 Agent harness 不漂移。
- MCP permission 持久化与 legacy option 兼容。
- 配置热刷新、缺省字段、旧服务响应和分层配置隔离。
- graceful shutdown 时 profile/trace 收尾。

Grok 的优势不是一个万能 E2E，而是大量针对状态不变量的窄测试。

### 2.5 配置兼容和隔离

`test_settings_refresh.rs` 用可运行 Mock HTTP 服务验证：

- 未配置 settings 返回 404，旧调用方退回 `None`。
- runtime mutation 对后续请求立即可见。
- 部分对象缺省字段保持 `None`，交给 resolver 逐字段回退。

`test_config_update_isolation.rs` 则固定一个关键不变量：

```text
effective config = user + managed + requirements
写回 user config 时只能读取 user layer
```

这防止高优先级 managed/requirements 值被意外固化进用户配置。测试使用串行执行，
避免进程级环境变量和全局缓存互相污染。

### 2.6 开源边界

仓库没有公开 `.github/workflows`，不能从源码确认：

- 合并门禁具体命令。
- 覆盖率阈值。
- flaky retry/quarantine 规则。
- 发布渠道、灰度比例和自动回滚指标。
- 数据库迁移与 SaaS 后端部署方式。

因此这些内容不能写成“Grok 已这样实现”，只能作为大厂 Agent 的目标设计。

## 3. EVERYDAYAIONE 当前实现

### 3.1 已有测试基础

当前仓库可见：

- 后端 `backend/tests` 有 359 个 `test_*.py` 文件。
- 前端有 118 个 `*.test.ts(x)` 或 `__tests__` 文件。
- 后端 pytest 使用 `asyncio_mode=auto`，默认忽略 `tests/manual`。
- 前端 Vitest 使用 jsdom 和 V8 coverage，四项全局阈值均为 80%。
- 当前 122 个迁移 SQL 中，119–135 有连续 17 个 rollback 文件。

已有重点覆盖：

- Chat tool loop、stream finalize、thinking、取消和中断历史恢复。
- 消息发送幂等重试和前端 optimistic rollback。
- Conversation Actor enqueue/claim/lease/fencing/progress/terminal/outbox。
- Web/企微 Sink、附件单次消费和多通道投递。
- 图片 Provider retry、任务恢复和 Redis 降级。
- 配置、权限模式、工具选择和 PromptBuilder。

### 3.2 Actor 测试的现有优势

119–135 迁移阶段形成了较完整的契约测试：

- 唯一键和请求幂等状态。
- serial/branch claim。
- lease 范围 `15..300s` 和 fencing token。
- commit/fail/cancel 的锁顺序与幂等。
- 进度更新必须属于当前 owner 且租约有效。
- rollback 先删除 RPC 再删除表/字段。
- Outbox、企微附件和 channel conversation 兼容约束。

此外 `ActorWebSink` 已测试 fencing 丢失时取消执行，Worker 测试覆盖 Redis 恢复、
扫描节流、任务锁释放和 graceful shutdown 的局部行为。

### 3.3 当前测试缺口

多数数据库迁移测试读取 SQL 文本后断言关键片段，优点是快速，缺点是不能证明：

- PostgreSQL 实际语法、类型和函数签名正确。
- 并发事务中的锁顺序、隔离级别和竞态结果正确。
- OrgScopedDB/RPC 权限包装与真实数据库行为一致。
- 旧 Schema 数据升级和 rollback 后仍可被旧代码读取。

项目更新记录显示部分迁移会人工做真实 PostgreSQL 单事务预演，这是重要补强，但目前
不是统一、自动、每次都执行的门禁。

当前也没有全局 Agent Trace fixture，把 Model response、Tool call、Action attempt、
RuntimeEvent、积分和 Artifact 一起重放。现有测试多以 Mock/patch 为主，容易分别证明
每段正确，却漏掉跨段协议断层。

### 3.4 当前发布脚本风险

`deploy/deploy.sh` 会执行前后端测试，但两处都是：

```text
test failed → log_warning → continue deploy
```

这使测试不是发布门禁。脚本随后通过 `rsync --delete` 覆盖服务器目录，并逐个
`systemctl restart`。已有健康检查和服务 active 检查，但仍缺少：

- 不可变 Release Artifact 和 commit SHA 校验。
- 数据库 schema compatibility gate。
- Actor Worker 停止认领、排空、再升级的正式协议。
- 按 org/user/cohort 的 canary。
- 自动比较错误率、Unknown Action、重复投递和积分异常。
- 失败后的自动流量回切与版本回滚。

`/api/health` 当前基础检查固定返回 ok；`/api/health/db` 捕获异常后仍返回普通 JSON，
未显式设置非 2xx。它们不足以表达 Runtime、Worker、Redis、Outbox backlog、Provider
和 schema revision 的 readiness。

## 4. 目标测试架构

### 4.1 六类确定性测试

| 层级 | 对象 | 必须验证 |
|---|---|---|
| Pure | reducer、resolver、policy、预算计算 | 同输入同输出、边界值 |
| Schema | Action、ToolOutput、RuntimeEvent、ContentPart | version、未知字段、兼容样例 |
| State machine | Run/Action/Goal/Interaction/Delivery | 合法转移、终态不可逆、幂等 |
| Contract | Tool/MCP/Provider/DB RPC/Channel | 请求映射、错误分类、重试语义 |
| Integration | PostgreSQL/Redis/Object store | 并发、崩溃窗口、恢复、权限 |
| E2E | Web/企微模拟器到 Artifact 展示 | 顺序、断线重连、降级和终态 |

状态机测试应采用表驱动或 model-based 方式，而不是只测试 happy path 方法调用。

### 4.2 Runtime Trace Bundle

每个回放 Fixture 建议固定：

```json
{
  "schema_version": 1,
  "clock_seed": "2026-07-18T00:00:00Z",
  "id_seed": 42,
  "config_snapshot": {},
  "catalog_revisions": {},
  "inputs": [],
  "model_responses": [],
  "tool_outputs": [],
  "provider_callbacks": [],
  "expected_events": [],
  "expected_state": {}
}
```

回放环境必须替换 wall clock、UUID、随机退避和模型输出。大文件内容存 Artifact fixture，
Trace 只保存 hash/URI。密钥和用户原文默认不得进入 Fixture。

事件比较分三层：

1. 必须完全相等：状态、sequence、correlation、cost ledger、terminal outcome。
2. 规范化后相等：时间、临时路径、Provider request ID。
3. 仅校验约束：自然语言展示、模型解释文本。

### 4.3 故障注入矩阵

至少覆盖以下 crash points：

| 故障点 | 预期 |
|---|---|
| claim 后、执行前崩溃 | lease 到期后可重新认领 |
| Provider 接受后、本地落库前断线 | Action 进入 Unknown，按查询/回调对账，禁止盲重试 |
| 扣费预留后调用失败 | 释放或退款且幂等 |
| Artifact 上传后消息提交失败 | 可由 hash/attempt 对账，不重复上传 |
| DB commit 后 WS 前崩溃 | Snapshot/Replay 恢复终态 |
| Outbox 外发成功后 checkpoint 前崩溃 | 明确 at-least-once 风险并去重/审计 |
| Worker SIGTERM | 停止 claim，续租已有任务并在期限内排空 |
| Redis/WS/Provider 抖动 | DB 事实不丢，退避有上限 |

故障注入不得只模拟抛异常，还应支持连接中断、超时、迟到响应、重复回调和乱序事件。

### 4.4 LLM Eval 独立运行

Eval 评估：

- 意图与 Tool 选择。
- 多段提示词拆成多个 Action 的正确率。
- 规划质量和完成验证。
- 不该执行时的克制率。
- Skill 遵循度。
- 不同模型/提示词 revision 的回归。

Eval 可以有统计波动，不得阻塞确定性测试的失败诊断。每个 Eval 样例记录模型、温度、
system prompt、Tool Catalog、Skill revision 和 Judge revision，避免只保存一个总分。

## 5. CI 与质量门禁

推荐流水线：

```text
PR
 ├─ lint / type / forbidden-any / file-size
 ├─ unit + schema + state-machine
 ├─ frontend coverage
 ├─ real PostgreSQL/Redis contract
 ├─ migration up/down/N-1 compatibility
 └─ selected deterministic traces

main/nightly
 ├─ full trace corpus
 ├─ channel E2E
 ├─ chaos and load
 ├─ provider smoke with cost cap
 └─ LLM eval regression
```

初始建议参数：

- PR 测试失败一律阻止合并和发布，不允许 warning 后继续。
- merge gate 不自动重试失败测试；确认 flaky 后隔离且设置到期日。
- Policy、状态机、协议模块分支覆盖目标不低于 90%；全局覆盖率保留 80%，但不能用全局
  高覆盖掩盖核心模块缺口。
- Provider smoke 默认每日或发布前执行，使用专用低额度账户和硬成本上限。
- Chaos/长时负载放 nightly，关键故障 Fixture 放每个 PR。

这些是目标初值，需用基线耗时和稳定性数据校准，不是 Grok 的公开参数。

## 6. 发布、迁移与回滚

### 6.1 Release Manifest

每个版本生成不可变清单：

```text
release_id / git_sha / build_digest
database_schema_revision
runtime_event_version
action/tool/plugin/skill schema versions
model + prompt catalog revisions
config catalog revision
migration set + checksum
test/eval evidence URI
```

运行记录引用 `release_id`，才能解释同一输入为何在不同版本产生不同执行路径。

### 6.2 数据库迁移

统一采用：

```text
expand → deploy compatible code → backfill
       → shadow read/verify → switch
       → observe one rollback window → contract
```

同一版本禁止“删除旧字段 + 新代码切换”。应用至少兼容 N/N-1 Schema。每个迁移需要：

- up/down SQL 和 checksum。
- 空库、生产形状样本、N-1 升级测试。
- 锁范围、预计时长、磁盘增长和回滚边界。
- 数据回填 checkpoint、速率和可重入键。
- rollback 是代码回切、Schema 回退还是仅关闭功能的明确分类。

有数据丢失风险的 contract migration 不能把 down SQL 当作真实恢复方案，必须依赖备份
或正向修复。

### 6.3 Actor Worker 升级

发布顺序建议：

1. 打开 maintenance claim gate，只阻止新 claim，不拒绝入队。
2. 等待 in-flight 到零或到达 drain timeout。
3. 未完成任务保持 DB lease，不强制写失败；旧 Worker 退出后由新 Worker 在 lease 到期
   后恢复。
4. 先部署兼容 Schema/Protocol 的 Worker，再部署 API/UI。
5. callback 和 Outbox consumer 在整个窗口保持可接收，必要时只暂停消费不丢记录。
6. readiness 成功后逐步恢复 claim。

`TimeoutStopSec=30` 只能作为进程上限，不能代替业务排空协议。

### 6.4 Canary 与自动回滚

Feature Flag 至少支持按 org、user cohort、channel、Action type 和 release revision
选择流量。Canary 不只看 HTTP 5xx，还要看：

- Run/Action terminal success ratio。
- Unknown/重复执行/重复投递比例。
- lease loss、恢复次数和 stuck age。
- Cost reservation 与最终结算差额。
- RuntimeEvent gap/replay failure。
- Artifact 缺失和 UI terminal convergence。
- 用户取消率和 permission denial 异常变化。

超过阈值时先关闭新 Runtime claim 或回切旧入口；Schema 保持 additive，使应用版本可
直接回滚。对已 Accepted/Unknown 的外部动作只能进入 reconciliation，不能随应用回滚
而再次执行。

## 7. 差距矩阵

| 能力 | Grok 可见源码 | EVERYDAYAIONE 当前 | 目标 |
|---|---|---|---|
| 单元/状态测试 | 大量窄不变量测试 | 数量多，Actor 契约较强 | 保留并统一状态模型 |
| Trace Replay | TodoGate 数据驱动样例 | 缺统一 Agent Trace | 全 Runtime Trace Bundle |
| Mock Provider | 独立 Mock inference server | 大量 patch/mock adapter | 共享协议级 fake server |
| 真实依赖 | 本地进程/PTY/stdio 较丰富 | PostgreSQL 多为人工预演 | PR 自动真实 DB/Redis |
| 配置兼容 | 热刷新、缺省、分层隔离 | 分散配置测试 | Config snapshot 兼容矩阵 |
| 前端测试 | 终端/PTY 体系丰富 | Vitest 80% 阈值 | 加 Web/企微端到端 |
| CI 证据 | 开源仓库未公开 | 无公开 workflow | 强制门禁 + Release Evidence |
| 发布 | SaaS 内部实现不可见 | rsync + systemd restart | 不可变构建 + canary |
| 测试失败行为 | 不可判断 | warning 后继续部署 | 必须阻断 |
| 迁移 | SaaS 后端不可见 | SQL + 近期 rollback | N/N-1 + expand/contract |
| 故障注入 | 多取消/恢复局部测试 | 局部异常测试 | crash-point 矩阵 |
| 自动回滚 | 不可判断 | 手工备份/恢复 | SLO gate + kill switch |

## 8. 边界场景

- 回放 fixture 的事件版本落后：先通过 version adapter，再比较目标协议。
- Provider 无查询接口：Unknown 只能等待签名回调或人工对账，禁止自动重试。
- canary org 同一会话跨新旧版本：Session/Run 创建时冻结 release revision。
- migration 回填和在线写并发：双写必须有统一幂等键和可重入 checkpoint。
- callback 在版本切换中到达：按 Action schema version 路由，不按当前默认版本猜测。
- rollback 后新字段仍存在：旧代码必须忽略新增字段，不读取不认识的终态。
- Eval 分数下降但正确性全绿：由产品门槛决定是否阻断，不混入运行时故障告警。
- 测试 Fixture 含敏感内容：提交前执行 schema-aware redaction 和 secret scan。

## 9. 推荐落地顺序

1. 立即把前后端测试失败改为发布硬失败，并记录 git SHA/迁移 checksum。
2. 为 Action、RuntimeEvent、CostLedger、Artifact 建纯状态机和 Schema 契约测试。
3. 建统一 Fake Model/Tool/Provider server 与虚拟时钟/ID。
4. 建第一批关键 Trace：明确执行、多 Action、仅写提示词、取消、Unknown、恢复。
5. 在 CI 启动真实 PostgreSQL/Redis，执行 119–135 up/down 和并发不变量。
6. 增加 Web 与企微 channel simulator E2E。
7. 引入 Release Manifest、Actor drain、org canary 和 kill switch。
8. 最后再扩展 nightly chaos、load 和 LLM Eval corpus。

## 10. 本层冻结结论

目标不是简单复制 Grok 测试目录，而是吸收其“窄不变量 + 可运行 fixture + 数据驱动回放”
的方法，并补齐 SaaS 自主执行系统必须具备的真实数据库、成本、副作用、灰度和回滚验证。

第十六层冻结为：

```text
Deterministic Correctness
  + Real Dependency Contracts
  + Trace Replay
  + Eval / Chaos
  + Release Evidence
  + Canary / Reconciliation / Rollback
```

下一层进入端到端业务链路，把前十五层协议串成“普通聊天、单工具、多工具、Skill、
Goal、Subagent、MCP、图片/视频、文件、企微和恢复”完整路径，并形成全项目差距矩阵。
