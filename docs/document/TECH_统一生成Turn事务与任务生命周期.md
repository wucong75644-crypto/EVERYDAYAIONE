# 技术设计：统一生成 Turn 事务与任务生命周期

> 状态：方案已确认，待开发 | 日期：2026-07-22
>
> 范围：Web Chat、图片、视频、电商图及企业微信生成入口

## 1. 背景与目标

生产错误 `TURN_MESSAGE_RELATION_MISMATCH` 的直接触发条件是历史助手消息缺少
`turn_id/reply_to_message_id`，而上一条用户消息已经有 `turn_id`。当前 Web 重试路径通过
“查询此前最近用户消息 + 新建随机 Turn”恢复关系，导致同一用户消息被要求绑定到两个 Turn。

根因不是数据库校验过严，而是生成请求的持久化被拆成多个事务：幂等 claim、用户消息、助手占位、
task 和 Turn 绑定分别完成；媒体任务还在供应商任务创建后才插入本地 task。任一步中断都可能留下
半写入消息或供应商孤儿任务。

本设计目标：

1. 用一个数据库原子入口建立生成请求的公共事实：Turn、输入消息、输出消息和本地 task。
2. Chat Actor、图片、视频和电商图继续保留各自执行状态机，不把外部调用放进数据库事务。
3. Retry/Regenerate 只使用显式关系恢复，不再以时间邻近和随机 UUID 作为权威关系。
4. 媒体供应商调用前必须存在可追踪的本地 task。
5. 对历史数据执行可审计、幂等、可回滚的确定性回填；不确定关系禁止自动修复。

非目标：

- 不修改前端 HTTP API 和消息展示协议。
- 不把图片/视频执行迁入 Conversation Actor。
- 不放宽 `bind_generation_turn` / `close_generation_turn` 的关系校验。
- 不在本次删除旧 RPC 或历史字段。

## 2. 项目上下文

### 2.1 架构现状

1. Web `/messages/generate` 已通过迁移 119 的 `message_generation_requests` 抢占幂等执行权，
   客户端为一次发送固定 request/task/user-message/assistant-message ID。
2. `_do_generate_message()` 仍分别调用 `create_user_message()`、`prepare_assistant_message()`、
   `resolve_existing_turn_anchor()` 和 Handler `start()`，公共事实不在同一事务内。
3. Chat 使用 `enqueue_generation_turn` 进入 PostgreSQL Conversation Actor；该 RPC 明确绑定
   `type='chat'`、queue sequence、lease 和 serial claim。
4. 图片/视频先锁积分并调用供应商，随后通过 `insert_task_with_turn_binding()` 插入 task；插入失败时
   只能退款，无法可靠追踪已经创建的供应商任务。
5. 企业微信 Actor 已有原子创建消息和 task 的先例，但同步旧链、电商图 Phase 1 仍存在直接写入旁路。

### 2.2 可复用模块

| 模块 | 复用方式 |
|---|---|
| `message_generation_requests` | 作为请求幂等权威记录，复用稳定客户端 ID |
| `claim_message_generation_request` | 保留请求抢占和响应重放语义 |
| `bind_generation_turn` | 继续作为兼容 task 绑定和严格不变量校验 |
| `enqueue_generation_turn` | 继续负责 Chat Actor task 入队，不扩展为媒体状态机 |
| `ContextAnchor` | 统一承载 RPC 返回的固定上下文基线 |
| 企微原子 enqueue RPC | 复用其行锁、幂等插入和冲突拒绝模式 |
| 现有积分锁定/退款 | 执行阶段继续使用，本次不重写积分账本 |

### 2.3 设计约束

- PostgreSQL 是 Turn/消息关系唯一裁决者；Python 不创建历史恢复 Turn。
- 外部供应商调用不得位于数据库事务内。
- 所有 RPC 必须 `SECURITY INVOKER`、固定 `search_path`、撤销 `PUBLIC` 执行权。
- 同一幂等请求重复执行必须返回相同 input/output/turn/task，不产生重复副作用。
- `org_id` 使用 `IS NOT DISTINCT FROM`，严格保持个人与组织 scope 隔离。
- 新迁移使用编号 148；当前工作区已有未提交迁移 147，不得覆盖。
- 修改文件超过 500 行前必须拆分；现有 `message_generation_helpers.py` 492 行、
  `image_handler.py` 484 行，不继续堆积生命周期逻辑。

### 2.4 潜在冲突

- 迁移 119 当前只 claim 幂等请求，不原子创建业务事实。
- `RETRY/REGENERATE_SINGLE` 复用助手消息，`SEND/REGENERATE` 是否创建用户消息的语义不同。
- 多图共享一个助手消息，但需要 1–4 个独立 task 和积分事务。
- 电商图 Phase 1 使用 image 类型 task，但执行方式是后台本地协程。
- 生产存在历史缺锚点消息；上线代码不能假设回填已覆盖全部历史数据。
- 当前工作区的 `CURRENT_ISSUES.md`、`FUNCTION_INDEX.md`、`PROJECT_OVERVIEW.md` 有用户未提交修改，
  实施时只允许定点追加。

## 3. 方案结论

采用“统一入口、公共事务内核、专用执行器”结构：

```text
HTTP / WeCom 请求
  -> 幂等 claim
  -> prepare_generation RPC（单事务）
       -> 锁 conversation + request
       -> 创建或验证 input message
       -> 创建或验证 output message
       -> 创建或验证 1..N local tasks
       -> 固定 turn/reply/base revision
       -> 返回权威 GenerationPreparation
  -> 事务提交
  -> 专用执行器
       -> Chat: Actor enqueue/wakeup
       -> Image/Video: lock credits -> provider submit -> attach external_task_id
       -> Ecom Plan: local async execution
  -> terminal RPC / 现有完成链
```

统一的是业务事务入口和关系不变量，不统一 Chat Actor 与媒体供应商的执行状态机。

## 4. 权威不变量

`prepare_generation` 必须在提交前验证：

1. conversation 的 user/org scope 与请求一致。
2. input message 必须是同 conversation/org 的 `user`。
3. output message 必须是同 conversation/org 的 `assistant`。
4. input/output/task 的 `turn_id` 完全一致。
5. output 的 `reply_to_message_id` 必须等于 input ID。
6. task 的 `assistant_message_id/input_message_id/conversation_id/org_id` 与消息一致。
7. 相同 request ID 重放时，operation、消息 ID、task ID 集合和请求指纹必须一致。
8. 已有显式关系不可被覆盖；冲突返回稳定业务错误码。
9. SEND/REGENERATE 创建新 Turn；RETRY/REGENERATE_SINGLE 复用原 Turn。
10. 媒体任务在供应商调用前处于本地 `preparing` 状态；供应商接受后才进入 `pending`。

## 5. 数据库设计

### 5.1 迁移 148

新增：

- `backend/migrations/148_unified_generation_prepare.sql`
- `backend/migrations/rollback/148_unified_generation_prepare_rollback.sql`

### 5.2 tasks 状态

现有 task 状态约束需加入 `preparing`（以生产真实约束为准，迁移前先查询约束定义）。

`preparing` 表示：本地任务与 Turn 已建立，但外部供应商尚未确认接受。Chat Actor 可直接创建为
`pending`；本地电商规划可创建为 `running`。

不新增表。`external_task_id` 保持可空；媒体接受后通过独立 RPC 原子附加。

### 5.3 RPC：prepare_generation

建议签名：

```sql
prepare_generation(
    p_request_id UUID,
    p_operation TEXT,
    p_conversation_id UUID,
    p_user_id UUID,
    p_org_id UUID,
    p_turn_id UUID,
    p_input_message JSONB,
    p_output_message JSONB,
    p_tasks JSONB
) RETURNS JSONB
```

约束：

- `p_tasks` 必须是 1–4 个对象的数组。
- `p_turn_id` 对新 Turn 由应用使用本次固定 ID 提供；数据库验证而不是盲目信任。
- Retry/Regenerate Single 的 `p_input_message` 只传 ID，不传新 content；RPC 从显式关系恢复。
- 新消息 payload 仅允许白名单字段，禁止用任意 JSONB 覆盖数据库列。
- 锁顺序固定：`message_generation_requests → conversations → input → output → tasks`。

返回：

```json
{
  "request_id": "uuid",
  "conversation_id": "uuid",
  "turn_id": "uuid",
  "input_message_id": "uuid",
  "output_message_id": "uuid",
  "base_context_revision": 12,
  "context_through_message_id": "uuid-or-null",
  "task_ids": ["uuid"],
  "already_prepared": false
}
```

稳定错误码：

| 错误码 | 场景 |
|---|---|
| `GENERATION_PREPARE_ARGUMENT_INVALID` | operation/payload/task 数量非法 |
| `GENERATION_PREPARE_REQUEST_MISMATCH` | request 与 conversation/user/org 不一致 |
| `GENERATION_PREPARE_MESSAGE_CONFLICT` | 已有消息 scope、role 或显式关系冲突 |
| `GENERATION_PREPARE_TURN_CONFLICT` | 已有 Turn 与请求 Turn 冲突 |
| `GENERATION_PREPARE_TASK_CONFLICT` | 幂等重放的 task 集合或字段不一致 |
| `GENERATION_PREPARE_ANCHOR_MISSING` | Retry 无法从显式关系/task 找到锚点 |

### 5.4 RPC：attach_generation_external_task

```sql
attach_generation_external_task(
    p_task_id UUID,
    p_external_task_id TEXT,
    p_credit_transaction_id UUID
) RETURNS JSONB
```

- 仅允许 `preparing → pending`。
- 相同值重放幂等；不同 external ID 返回冲突。
- 验证积分事务确实属于当前 user/org/task 请求；如现有积分事务使用临时 task ID，实施阶段需先
  将 `_lock_credits` 改为使用已经生成的本地 task ID。
- external task ID 应保持/补充唯一索引；迁移前审计历史重复值。

### 5.5 RPC：fail_prepared_generation_task

供应商明确拒绝或调用失败时，把 `preparing` task 原子更新为 `failed`，记录终态原因。积分退款继续由
现有积分账本 RPC 完成；退款失败保留 failed task 和 transaction ID，供补偿任务处理。

## 6. 操作语义

| Operation | Input | Output | Turn |
|---|---|---|---|
| SEND | 按固定 user message ID 新建 | 按固定 assistant ID 新建 | 新建固定 Turn |
| REGENERATE | 创建新的用户消息和助手消息（保持现有产品语义） | 新建 | 新建固定 Turn |
| RETRY | 从原助手显式 `reply_to` 或已绑定 task 取得 | 复用原助手 | 复用原 Turn |
| REGENERATE_SINGLE | 同 RETRY | 复用原多图助手 | 复用原 Turn，新增指定 index task |

历史助手既无 `reply_to` 又无 task 锚点时，在线 RPC 返回 `GENERATION_PREPARE_ANCHOR_MISSING`；不得
回退为“最近用户消息”。若历史回填已经建立显式关系，则正常重试。

## 7. 应用层设计

### 7.1 新服务模块

新增 `backend/services/generation_lifecycle.py`，原因是现有路由和 Handler 接近文件长度阈值。

职责：

- 构造白名单 RPC payload。
- 解析 `GenerationPreparation`。
- 调用 prepare/attach/fail RPC。
- 记录带 request/user/org/conversation/task/turn 的结构化日志。
- 不进行消息时间推断，不调用供应商，不处理 UI 响应。

### 7.2 Web 路由

`_do_generate_message()` 调整为：

1. 解析 generation type、权限、模型和预检。
2. 生成本次稳定 Turn 与 task ID 集合。
3. 调用 `GenerationLifecycle.prepare()`。
4. 使用返回的消息模型和 `ContextAnchor` 启动专用 Handler。

删除主链对以下函数的依赖：

- `create_user_message`
- `prepare_assistant_message`
- `resolve_existing_turn_anchor`

这些函数在兼容窗口保留，但主生成入口不得再调用。

### 7.3 Chat Actor

`enqueue_web_chat()` 接收 prepare 返回的已存在 task/锚点。现有 `enqueue_generation_turn` 需要增加
“验证并入队已准备 Chat task”的兼容路径，或新增薄 RPC；不得再次改写消息关系。

### 7.4 图片/视频

每个媒体 task 的顺序固定为：

```text
local task(preparing) 已存在
  -> 使用 local task ID 锁积分
  -> provider.generate
  -> attach external ID + transaction ID
  -> pending
```

失败时：

```text
provider 明确失败
  -> fail local task
  -> refund transaction
  -> 按批次汇总成功/失败
```

供应商超时且结果未知时不得立即把 task 删除；保持 `preparing` 并记录 `submission_unknown`，由现有
轮询/补偿机制按 provider 支持能力处理，避免重复提交。

### 7.5 电商图与企微

- 电商图 Phase 1 使用统一 prepare 创建 `running` 本地 task，不再调用
  `_insert_task_with_turn_binding()`。
- 电商图 Phase 2 复用图片多 task 流程。
- Web 用户绑定的企微入口和企微原生 Actor wrapper 必须调用相同公共 RPC 或公共数据库内核。
- 企微现有 delivery context、附件消费和 outbox 协议保持不变。

## 8. 历史回填设计

新增 `backend/scripts/backfill_generation_turns.py`，放在现有运维脚本目录，避免迁移事务执行长时间历史扫描。

### 8.1 权威来源优先级

1. 已绑定 task：`task.input_message_id + task.assistant_message_id + task.turn_id`。
2. 助手已有 `reply_to_message_id`，且 input/output scope 与 role 合法。
3. 用户与助手已有相同非空 Turn。
4. 只有当同 conversation 中候选输入唯一、创建时间关系明确、期间不存在其他 user/assistant，才允许
   作为低优先级确定性配对。
5. 其他情况记为 ambiguous，不修改。

### 8.2 执行协议

- 默认 dry-run；只有 `--apply` 才写入。
- keyset 分页，不使用大 OFFSET。
- 每批独立事务，失败批次不推进 checkpoint。
- apply 仅在维护窗口使用 `FOR UPDATE`；禁用 `SKIP LOCKED`，避免 keyset checkpoint
  越过被锁历史行。
- 输出 scanned/repaired/already_valid/conflict/ambiguous/failed 及原因分类。
- 幂等重跑；任何已有非空冲突值都不覆盖。
- apply 前后分别运行数据库不变量审计 SQL。

### 8.3 回滚

回填 apply 前把拟修改的 message ID、旧值、新值写入受控审计文件；回滚脚本仅在当前值仍等于本次
新值时恢复旧值，避免覆盖上线后的合法更新。审计文件不得包含消息正文。

## 9. 边界与极限情况

| 场景 | 处理策略 | 模块 |
|---|---|---|
| 相同请求并发 | request 行锁 + task/message 幂等校验，返回同一 preparation | RPC |
| Retry 连点 | 复用原 Turn；不同 task ID 由请求幂等层裁决 | RPC/API |
| 历史助手无锚点 | 返回明确 409，不猜测；回填后再试 | RPC/API |
| 用户有 Turn、助手无 Turn | 仅显式/确定性回填；在线不生成随机 Turn | 回填/RPC |
| 多图部分供应商失败 | 每个 task 独立终态与退款，助手消息保留批次聚合语义 | ImageHandler |
| 供应商成功、attach 失败 | 本地 preparing task 保留；按 task/request 日志补偿，不重复提交 | Media/SRE |
| 供应商超时结果未知 | 标记 submission_unknown，禁止自动重复外部副作用 | Media |
| 积分锁定失败 | task 置 failed，供应商不调用 | Credit/Media |
| 退款失败 | task 保留 transaction ID，CRITICAL 包含业务上下文，进入补偿 | Credit |
| Chat 入队失败 | preparation 可幂等重放，再次入队不重复消息/task | Actor |
| 用户取消 | 仅取消尚未外部接受的 preparing task；已接受走现有取消协议 | Task |
| 跨组织 ID 注入 | RPC scope 校验失败关闭 | RPC/Security |
| 大规模回填 | keyset、小批事务、checkpoint、dry-run | Script |
| 新旧版本同时运行 | 先迁移，旧 RPC 保留；新代码只走新入口 | Deploy |

## 10. 连锁修改清单

| 改动点 | 影响文件 | 必须同步修改 |
|---|---|---|
| 新 prepare RPC | migration 148、migration tests | 正向/回滚、权限、幂等、scope 测试 |
| 生命周期服务 | `services/generation_lifecycle.py`、Base/ContextAnchor | 类型化返回与错误映射 |
| Web 主链收口 | `message.py`、request preparation、generation helpers | 移除分步创建和 fallback 调用 |
| Chat 已准备 task 入队 | `chat/actor_enqueue.py`、Actor migration/RPC tests | 不重复插 task/改消息 |
| 媒体 task 先落库 | image/video handlers、turn binding | 使用 local task ID、attach/fail |
| 多图稳定 task ID | image handler/tests | 以 request/client task + index 确定 |
| 电商图旁路 | ecom image handler/tests | Phase 1/2 使用统一 preparation |
| 企微旁路 | wecom lifecycle/actor enqueue/migration tests | 复用公共数据库内核 |
| 删除主链时间推断 | message turn anchors/tests | 兼容函数不再被生产入口引用 |
| 历史回填 | script + tests | dry-run/apply/checkpoint/审计/回滚 |
| 文档 | overview/function index/current issues/本设计 | 同步状态与函数索引 |

## 11. 架构影响评估

| 维度 | 评估 | 风险 | 应对措施 |
|---|---|---:|---|
| 模块边界 | 公共生命周期与执行器职责分离 | 中 | 新增独立 service，不扩展大型 Handler |
| 数据流向 | 分步写入改为单向 prepare→execute→terminal | 中 | 类型化 preparation，禁止反向猜测 |
| 扩展性 | 每请求锁固定少量行，多图最多 4 task | 低 | 固定锁顺序、索引命中、无历史扫描 |
| 耦合度 | 所有生成入口依赖公共内核 | 中 | RPC 只定义公共事实，不含 provider/Actor 分支 |
| 一致性 | 与企微原子 enqueue、幂等 claim 模式一致 | 低 | 复用 SECURITY/JSONB/错误码约定 |
| 可观测性 | 可按 request/turn/task 贯穿日志 | 低 | 增加 preparation outcome 和状态滞留指标 |
| 可回滚性 | additive migration，新旧 RPC 暂时并存 | 中 | 先迁移后代码，应用可回滚，数据不删除 |

## 12. API 兼容

HTTP 路径、请求体和 `GenerateResponse` 不变。新增内部错误映射：

- 明确历史锚点缺失：HTTP 409，提示该历史消息暂不能重试，不返回 500/CRITICAL。
- scope/真实关系冲突：HTTP 409，并记录结构化安全/一致性日志。
- 数据库不可用：保持 5xx，幂等请求不伪装成功。

前端无需修改；现有 Idempotency-Key 和固定消息 ID 继续使用。

## 13. 可观测性

新增结构化事件：

- `generation_prepared`
- `generation_prepare_replayed`
- `generation_prepare_conflict`
- `media_submission_attached`
- `media_submission_unknown`
- `prepared_task_failed`

必要字段：`request_id/user_id/org_id/conversation_id/turn_id/task_id/provider/operation`。

上线指标：

- `TURN_MESSAGE_RELATION_MISMATCH` 新增次数必须为 0。
- `preparing` 超过 5 分钟的 task 数。
- provider 已接受但缺 external ID 的补偿数。
- prepare replay/conflict 比率。
- 回填 repaired/ambiguous/conflict 数。

错误监控 fingerprint 应忽略 task/batch/transaction UUID，避免同一根因产生 31 个 fingerprint；此项作为
同批可观测性修复，但不改变业务错误处理。

## 14. 部署与回滚

### 14.1 部署顺序

1. 在目标生产迁移基线上确认 119–147 实际应用状态。
2. 执行迁移 148，保留所有旧 RPC。
3. 运行 migration contract tests 和只读不变量审计。
4. 部署后端，新 Web/企微入口切换到 `prepare_generation`。
5. 观察 prepare、preparing task、退款和关系冲突指标。
6. 执行历史回填 dry-run，审核分类结果。
7. 维护窗口分批 apply 可确定数据并复查。
8. 稳定期后另立任务删除旧主链和旧 RPC，当前版本不删除。

### 14.2 应用回滚

- 回滚后端到旧版本；旧 RPC 仍存在，可继续运行。
- 新增的 `preparing` task 不删除，由补偿脚本按状态处理。
- 不回滚已经建立的合法 Turn/消息关系。

### 14.3 数据库回滚

- rollback 只撤销新 RPC/权限和 `preparing` 约束扩展。
- 若仍存在 `preparing` task，回滚约束前必须先将其安全终态化，否则停止回滚。
- 不删除任务、消息、Turn 或回填后的合法关系。

## 15. 开发任务拆分

### Phase 1：数据库公共内核

- [ ] 148 正向/回滚迁移与 contract tests。
- [ ] prepare/attach/fail RPC 的幂等、scope、并发和错误码测试。
- [ ] 新增 `GenerationPreparation` 类型及生命周期 service。

### Phase 2：Web 与 Chat Actor

- [x] Web Chat SEND/REGENERATE/RETRY/REGENERATE_SINGLE 切换统一 prepare。
- [x] Chat Actor 改为消费已准备 task，不重复生成内部 task ID。
- [x] 删除 Web Chat 主链对时间邻近 fallback 的调用。

### Phase 3：媒体执行器

- [x] 普通图片单图/多图 task 在 provider 前落库。
- [x] 视频 task 在 provider 前落库。
- [x] 电商图 Phase 1/2 消除直接 task 绑定旁路。
- [x] 积分锁定改用 local task ID，补齐 attach/fail/unknown 状态。

### Phase 4：企微统一与历史治理

- [ ] 企微 Actor/同步入口复用公共数据库内核。
- [x] 回填脚本、dry-run 报告、checkpoint 和无正文条件审计。
- [ ] 生产不变量审计 SQL 与运行手册。

### Phase 5：验证与文档

- [ ] 调用 `/everydayai-test-coverage`，先定向再按风险升级。
- [ ] 调用 `/everydayai-review` 完成代码、安全、运行和回滚审查。
- [ ] 更新 FUNCTION_INDEX、PROJECT_OVERVIEW、CURRENT_ISSUES 和本设计状态。

## 16. 测试策略

最低覆盖：

- migration SQL contract 与 rollback 对称性。
- RPC 相同请求幂等重放、不同 payload 冲突、跨 tenant 拒绝。
- 两个并发 prepare 只产生一组消息/task。
- 四种 operation 的 Turn 语义。
- 单图、多图部分失败、视频、Ecom Phase 1/2。
- provider 明确失败、超时未知、attach 失败、退款失败。
- Chat Actor enqueue/claim/terminal 回归。
- 历史 79 类冲突样本的匿名化 fixture 回归。
- 回填 dry-run 无写入、apply 幂等、ambiguous 不修改、条件回滚。

不得默认执行 External 测试；生产供应商验证只在部署阶段用低成本任务人工确认。

## 17. 风险与缓解

| 风险 | 严重度 | 缓解措施 |
|---|---:|---|
| 新旧代码并存产生不同 task 状态 | 高 | 先迁移后代码、旧 RPC 保留但新入口单写、部署窗口检查活跃请求 |
| 历史关系误回填 | 高 | 权威来源分级、默认 dry-run、ambiguous 不写、条件回滚 |
| provider 超时导致重复提交 | 高 | preparing/submission_unknown 持久状态，禁止无依据重试外部副作用 |
| 多图共享消息导致 task 冲突 | 中 | 稳定 request+index task ID，RPC 验证完整 task 集合 |
| 行锁死锁 | 中 | 固定锁顺序、单会话少量行、并发测试 |
| 文档/迁移与现有未提交改动冲突 | 中 | 使用迁移 148、外科式追加、不重写用户文件 |

## 18. 文档更新清单

- [x] 新增本技术设计。
- [x] `docs/PROJECT_OVERVIEW.md` 登记设计文档。
- [x] `docs/CURRENT_ISSUES.md` 记录根因、生产证据和待开发状态。
- [ ] 实施新增/修改函数后更新 `docs/FUNCTION_INDEX.md`。
- [ ] 架构落地后更新 `docs/document/TECH_ARCHITECTURE.md`。

## 19. 设计自检

- [x] 架构现状、可复用模块、设计约束和潜在冲突完整。
- [x] 已追踪 Web、Chat Actor、图片、视频、电商图和企微调用链。
- [x] 已覆盖失败、空值、并发、超时、重试、降级、取消和历史回填。
- [x] 已定义数据库协议、API 兼容、部署和回滚。
- [x] 所有连锁修改已进入任务拆分。
- [x] 不新增依赖，不放宽既有关系校验。
