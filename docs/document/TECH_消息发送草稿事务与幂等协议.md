# 技术设计：消息发送草稿事务与幂等协议

> 状态：待确认实施
>
> 日期：2026-07-16
>
> 范围：Web 端文字、图片、视频、电商图统一发送链路

## 1. 背景与目标

当前输入提交在 `await handle*Message()` 成功后才清空文本和附件。消息区已经在 HTTP 请求前执行乐观更新，因此后端响应较慢时会出现“消息已经发出，但输入框仍保留原内容”的错觉。文字对话同样存在，只是通常响应较快而不明显。

本设计同时解决两个问题：

1. 前端校验通过后立即清空输入区，并在明确拒绝时无损恢复草稿。
2. 网络超时或断线后使用相同幂等键安全重试，避免重复消息、重复任务和重复扣费。

非目标：

- 不改变模型生成、WebSocket 内容推送和任务终态协议。
- 不把编辑器草稿放入全局消息 Store。
- 不因为模型最终生成失败而恢复输入框。
- 不引入新的前端状态管理或请求依赖。

## 2. 行业标准依据

本方案采用“乐观 UI + 幂等写入 + 不确定结果重试/对账”模式：

- React `useOptimistic`：异步操作期间展示乐观状态，失败后回到真实状态。
- IETF `Idempotency-Key` 草案：客户端生成唯一键；相同键、相同请求重放原结果；相同键、不同请求拒绝；并发重复请求返回冲突。
- AWS Builders' Library：幂等记录必须在业务副作用前取得执行权，相同客户端令牌保证最多一次业务效果。
- Stripe：连接错误后使用相同幂等键安全重试，并重放首次请求的状态和响应。

IETF 文档目前仍是 Internet-Draft，不是正式 RFC；本设计采用的是已被 AWS、Stripe 等生产系统验证的通行语义。

## 3. 项目上下文

### 3.1 架构现状

1. `InputArea` 组合文本、图片、文件、工作区文件和音频状态，`useInputSubmission` 负责提交和成功后清理。
2. `sendMessage` 在 HTTP 前通过 `applyOptimisticUpdate` 创建用户乐观消息和助手占位，并提前订阅 `client_task_id`。
3. 后端 `/messages/generate` 在返回前完成预检、用户消息、助手占位和任务创建；后续结果通过 WebSocket 和任务恢复处理。
4. `/tasks/pending` 已支持刷新恢复和 WebSocket 重连补偿。
5. `client_request_id` 当前只有普通索引，只用于消息匹配，不具备服务端幂等语义。

### 3.2 可复用模块

| 模块 | 复用方式 |
|---|---|
| `applyOptimisticUpdate` | 继续负责消息区本地乐观状态 |
| `processApiResponse` | 继续负责 HTTP 接受后的任务映射 |
| `rollbackOnError` | 调整为按发送结果分类回滚 |
| `client_task_id` / `assistant_message_id` | 自动重试时保持不变 |
| `/tasks/pending` | 页面刷新和 WS 重连后的最终恢复 |
| `ApiRequestError` | 扩展保留 HTTP/网络/超时分类信息 |
| `AppException` | 返回统一幂等错误码 |

### 3.3 设计约束

- React + TypeScript + Zustand、FastAPI + PostgreSQL 技术栈不变。
- API 响应继续使用项目统一错误信封。
- `InputArea.tsx` 当前 499 行，禁止继续堆积状态机逻辑。
- 所有自动重试必须复用同一请求的全部客户端 ID。
- 图片 Blob、File 和 Object URL 不复制、不重新上传。
- 数据库变化必须提供正向和回滚迁移。

### 3.4 潜在冲突

- 当前 `rollbackOnError` 对积分不足、同步图片失败和普通错误的消息保留策略不同，但输入层一律保留草稿。
- Axios 无响应错误被统一转换为 `NETWORK_ERROR`，丢失“超时/断网/有 HTTP 响应”的差异。
- 后端用户消息、助手占位和任务通过多次数据库操作完成，不是单个数据库事务。
- `client_request_id` 没有唯一约束，重复请求会创建新的用户消息。

## 4. 统一状态模型

### 4.1 草稿状态

```ts
type DraftSubmissionStatus = 'editing' | 'submitted' | 'uncertain';

interface DraftSnapshot {
  requestId: string;
  conversationId: string | null;
  prompt: string;
  imageUrls: string[];
  imageInputs: ImageInputInfo[];
  fileInputs: UploadedFileInput[];
  workspaceFiles: WorkspaceFile[];
  submittedAt: number;
}
```

- `editing`：显示当前草稿，允许编辑。
- `submitted`：本次草稿已冻结，输入区立即显示为空。
- `uncertain`：请求没有得到明确响应，保留冻结快照且禁止直接生成新请求。

快照只持有当前对象引用。成功后调用现有清理函数释放资源；明确拒绝时不清理底层状态，直接重新显示。

### 4.2 发送结果

```ts
type SendDisposition = 'accepted' | 'replayed' | 'processing';
type SendFailureDisposition = 'rejected' | 'recorded_failure' | 'uncertain';

interface SendResult {
  disposition: SendDisposition;
  clientRequestId: string;
  clientTaskId: string;
  assistantMessageId: string;
  retryAfterMs?: number;
}
```

| 结果 | 含义 | 草稿处理 |
|---|---|---|
| `accepted` | 首次请求已创建任务 | 永久清理 |
| `replayed` | 重试命中已完成幂等记录 | 永久清理 |
| `processing` | 原请求仍在处理 | 保持冻结并按 `details.retry_after` 重试 |
| `rejected` | 明确未接受的业务拒绝 | 恢复显示 |
| `recorded_failure` | 已形成用户消息和失败助手消息 | 永久清理 |
| `uncertain` | 无响应、超时、连接中断 | 保持冻结，使用相同键重试 |

## 5. 前端流程

实现状态：已落地。文本与三类附件在校验后通过可逆 detach 事务立即移出；明确拒绝执行合并恢复，成功、已记录失败和结果未知均不恢复旧草稿。

异常与保留期补强：普通未知异常最佳努力保存统一、脱敏的 500 终态；进程强杀不自动接管仍在 `processing` 的请求。数据库函数 `cleanup_expired_message_generation_requests()` 由 API lifespan 每小时调用，删除 `expires_at < NOW()` 的记录，确保 24 小时保留期实际生效。

```text
点击发送
  -> 前端校验
  -> 创建本次固定 IDs
  -> 冻结 DraftSnapshot
  -> 输入区立即显示为空
  -> 创建对话（如需要）
  -> sendMessage(snapshot, ids)
       -> 消息区乐观更新
       -> POST /generate（Idempotency-Key）
       -> accepted/replayed：提交草稿清理
       -> processing：延迟后复用相同请求重试
       -> rejected：恢复草稿
       -> recorded_failure：保留失败消息，清理草稿
       -> uncertain：冻结草稿并安全重试
```

### 5.1 固定 ID

一次用户点击只生成一次：

- `clientRequestId`
- `clientTaskId`
- `userMessageId`
- `assistantMessageId`

首次请求、401 刷新重发、网络自动重试必须复用这些 ID 和完全相同的业务请求体。用户主动修改内容重新发送时才创建新 ID。

### 5.2 自动重试

- 范围：无 HTTP 响应、超时、502、503、504。
- 次数：最多 2 次。
- 退避：500ms、1500ms，并加入不超过 20% jitter。
- 429：遵循 `Retry-After`；没有该响应头时不自动重试。
- 409 `IDEMPOTENCY_REQUEST_IN_PROGRESS`：按统一错误信封中的 `details.retry_after` 查询同一请求，最多持续到现有 60 秒发送确认预算。
- 业务 4xx：不重试。

### 5.3 新对话

创建对话失败发生在消息发送之前，属于明确拒绝：恢复草稿。创建成功但发送结果未知时，快照绑定新对话 ID，后续重试不得再次创建对话。

### 5.4 对话切换

快照绑定 `requestId + conversationId`。旧请求完成时只能结算自己的快照，不得清空新对话或用户后来输入的内容。

## 6. API 协议

### 6.1 请求

接口保持：

```http
POST /api/conversations/{conversation_id}/messages/generate
Idempotency-Key: "<client_request_id>"
```

请求体在兼容期继续携带 `client_request_id`。后端规则：

- Header 和 body 都存在时必须相等，否则返回 400 `IDEMPOTENCY_KEY_MISMATCH`。
- 旧客户端仅传 body 时继续接受。
- 新客户端必须发送 Header。

### 6.2 请求指纹

对下列字段稳定 JSON 序列化并计算 SHA-256：

```text
conversation_id, operation, content, generation_type, model,
业务 params, original_message_id, assistant_message_id, client_task_id
```

指纹必须排除服务端运行时字段：

```text
_task_slot_id, _prefetched_summary, _org_id, _user_location
```

字典键排序，数组保持原顺序，JSON 使用 UTF-8 和固定分隔符。

### 6.3 重复请求响应

| 场景 | HTTP | 错误码/结果 |
|---|---:|---|
| 首次请求 | 200 | 正常 `GenerateResponse` |
| 相同键、相同指纹、已完成 | 200 | 重放原响应，标记 `replayed=true` |
| 相同键、相同指纹、处理中 | 409 | `IDEMPOTENCY_REQUEST_IN_PROGRESS` + `details.retry_after` |
| 相同键、不同指纹 | 422 | `IDEMPOTENCY_KEY_REUSED` |
| Header/body 不一致 | 400 | `IDEMPOTENCY_KEY_MISMATCH` |
| 已记录业务失败 | 原状态 | 重放原错误状态和错误体 |

重放返回与首次成功语义一致的原始 `GenerateResponse`，不增加响应字段；前端统一按 accepted 处理。

## 7. 数据库设计

迁移：`backend/migrations/119_message_generation_idempotency.sql`

回滚：`backend/migrations/rollback/119_message_generation_idempotency_rollback.sql`

### 7.1 表结构

```sql
CREATE TABLE message_generation_requests (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id UUID NULL,
    user_id UUID NOT NULL,
    conversation_id UUID NOT NULL,
    idempotency_key VARCHAR(100) NOT NULL,
    request_fingerprint CHAR(64) NOT NULL,
    status VARCHAR(20) NOT NULL
        CHECK (status IN ('processing', 'completed', 'failed')),
    client_task_id VARCHAR(100) NOT NULL,
    user_message_id UUID NULL,
    assistant_message_id UUID NOT NULL,
    response_status SMALLINT NULL,
    response_body JSONB NULL,
    error_code VARCHAR(100) NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL DEFAULT NOW() + INTERVAL '24 hours'
);
```

### 7.2 索引

PostgreSQL 对 `NULL` 的唯一语义需要分开处理：

```sql
CREATE UNIQUE INDEX uq_message_req_org_key
ON message_generation_requests (org_id, user_id, idempotency_key)
WHERE org_id IS NOT NULL;

CREATE UNIQUE INDEX uq_message_req_personal_key
ON message_generation_requests (user_id, idempotency_key)
WHERE org_id IS NULL;

CREATE INDEX idx_message_req_expiry
ON message_generation_requests (expires_at);
```

外键按项目现有数据库约束策略评估后再加入；本功能不依赖级联删除。

### 7.3 原子抢占 RPC

新增返回 JSONB 的 PostgreSQL RPC `claim_message_generation_request(...)`，以兼容项目现有 `db.rpc()` 的 `SELECT function(...)` 调用方式：

1. 尝试插入 `processing`。
2. 唯一冲突时读取已有记录。
3. 返回 `claimed / processing / completed / failed / fingerprint_mismatch`。

RPC 只负责幂等执行权，不调用外部模型。后续每一步使用固定 ID，因此进程中断后可以根据消息和任务表恢复幂等记录。

### 7.4 过期与卡死处理

- 记录保留至少 24 小时。
- 清理只删除 `expires_at < now()` 且非 `processing` 的记录。
- `processing` 超过发送确认预算时，先按 `assistant_message_id` 和 `client_task_id` 查询消息/任务：
  - 找到任务：补写 completed 响应。
  - 找到失败消息：补写 failed 响应。
  - 均不存在：标记 failed，允许客户端以新请求重新发送。

## 8. 后端模块设计

新增 `backend/services/message_idempotency_service.py`，职责仅包括：

- 提取并验证幂等键。
- 计算请求指纹。
- 调用原子抢占 RPC。
- 重放已完成/失败响应。
- 记录用户消息、助手消息和任务 ID。
- 完成、失败和卡死恢复。

`message.py` 的顺序调整为：

```text
解析请求身份和指纹
  -> claim 幂等执行权
  -> 命中旧请求则直接重放
  -> task slot 检查
  -> 现有 _do_generate_message
  -> 保存成功响应
  -> 返回
```

明确业务拒绝发生时保存失败响应，重复请求重放同一错误。无法确认是否落库的异常不得覆盖为普通失败，由恢复逻辑核对。

## 9. 错误分类

前端 `ApiRequestError` 增加：

```ts
transport: 'http' | 'timeout' | 'network';
retryAfterMs?: number;
sendDisposition?: SendFailureDisposition;
```

当前实现由后端统一错误体的 `details.retry_after` 计算 `retryAfterMs`；未提供时使用 500ms、1500ms 两档退避，最多重试 2 次。业务错误码（例如 `IMAGE_GENERATION_FAILED`）即使采用 502 状态也不按网关错误重试。

分类规则：

| 条件 | 分类 |
|---|---|
| 幂等服务确认未产生业务副作用的业务拒绝 | `rejected` |
| 后端返回统一失败媒体消息或已记录失败响应 | `recorded_failure` |
| timeout / 无 response / 连接中断 | `uncertain` |
| 502/503/504 | 先按 `uncertain` 使用相同键重试 |

## 10. 边界场景

| 场景 | 处理策略 |
|---|---|
| 空文本但有附件 | 快照附件并正常发送 |
| 图片仍上传 | 前端拦截，不进入提交状态 |
| 图生图无参考图 | 前端拦截，不进入提交状态 |
| 积分不足 | 后端记录可重放拒绝；消息回滚；恢复草稿 |
| 任务提交前图片失败且已形成失败卡片 | 不恢复草稿，允许消息原地重试 |
| HTTP 响应丢失但任务已创建 | 相同 key 重试并重放成功响应 |
| 同 key 不同内容 | 422，恢复当前草稿并记录安全日志 |
| 双击发送 | 前端拦截；后端唯一键兜底 |
| 401 | 现有 silent refresh 重发同一 Axios config 和幂等键 |
| 429 | 不生成新 key；遵循 `Retry-After` |
| 用户切换对话 | 请求只结算绑定快照，不影响新草稿 |
| 页面刷新 | 消息和任务由现有恢复机制接管；幂等记录用于安全重试 |
| 停止生成 | 任务取消，不恢复已接受的草稿 |

## 11. 连锁修改清单

| 改动点 | 文件 | 同步内容 |
|---|---|---|
| 草稿事务 | `useInputSubmission.ts` | 快照、状态、成功清理和拒绝恢复 |
| 输入展示 | `InputArea.tsx` | 使用提交 Hook 返回的展示状态，保持文件不超过 500 行 |
| 固定请求 ID | `messageSender.ts` | 接受外部 IDs，自动重试复用请求 |
| 生命周期分类 | `messageSendLifecycle.ts` | 分类回滚，unknown 不立即删除乐观状态 |
| 错误元数据 | `api.ts` | 保留 timeout/network/status/Retry-After |
| 请求/响应类型 | `schemas/message.py` | `replayed` 和幂等键校验 |
| 幂等入口 | `api/routes/message.py` | claim、重放、成功/失败记录 |
| 幂等服务 | 新增 `message_idempotency_service.py` | 指纹和状态机 |
| 数据库 | 新增 119 正向/回滚迁移 | 表、索引、RPC |
| 文档 | `FUNCTION_INDEX.md`、`PROJECT_OVERVIEW.md`、`CURRENT_ISSUES.md` | 模块和进度同步 |

## 12. 架构影响评估

| 维度 | 评估 | 风险 | 应对 |
|---|---|---:|---|
| 模块边界 | 草稿留在输入 Hook，幂等集中到后端 Service | 低 | 不侵入消息 Store |
| 数据流 | 增加幂等记录，原消息/任务流不变 | 中 | 固定 IDs + 专项测试 |
| 一致性 | 多表操作不是单事务 | 中 | 原子 claim + 可恢复状态机 |
| 并发 | 同 key 并发争抢 | 中 | 数据库唯一索引和 RPC |
| 性能 | 每次发送增加一次 claim 和一次终态写入 | 低 | 索引命中，常数级开销 |
| 可观测性 | 增加请求状态和重放次数 | 低 | 日志统一带 request/task/message ID |
| 可回滚性 | 新表为旁路能力 | 低 | 先回滚代码，再执行 drop 迁移 |

## 13. 开发任务拆分

### 阶段 A：数据库与后端幂等

1. 新增 119 正向、回滚迁移和 claim RPC。
2. 新增 `MessageIdempotencyService` 及单元测试。
3. 接入 `/messages/generate`，实现重放、冲突和失败记录。
4. 补充并发相同 key、不同指纹、处理中和卡死恢复测试。

### 阶段 B：前端发送协议

1. `messageSender` 接受固定 IDs 并返回结构化结果。
2. `api.ts` 保留 transport 和 `Retry-After`。
3. `messageSendLifecycle` 完成三类失败处理。
4. 增加相同 key 自动重试和重放测试。

### 阶段 C：草稿事务

1. `useInputSubmission` 创建冻结快照和状态机。
2. `InputArea` 接入立即清空展示。
3. 覆盖文本、图片、视频、电商图、文件和工作区附件。
4. 覆盖切换对话和旧请求回调竞态。

### 阶段 D：集成验证与文档

1. 运行前后端专项及相关全量测试。
2. 执行 `/everydayai-test-coverage` 补齐覆盖。
3. 检查 500/120/15/4 质量阈值和调用方签名。
4. 更新函数索引、项目概览和当前问题状态。

## 14. 测试验收矩阵

1. HTTP 延迟期间输入区立即为空。
2. 成功后底层文本和附件正式清理。
3. 积分、权限、参数拒绝后完整恢复。
4. 已形成失败卡片时不恢复草稿。
5. 超时重试只创建一条用户消息、一个助手消息和一个任务。
6. 同 key 同 payload 返回相同消息/任务 ID。
7. 同 key 不同 payload 返回 422。
8. 并发相同 key 只有一个执行者。
9. 重放失败不重复扣费。
10. 401 刷新、429、502/503/504 符合重试策略。
11. 页面刷新和 WS 重连不产生重复占位。
12. 切换对话后旧请求不清理新草稿。

## 15. 部署与回滚

部署顺序：

1. 备份数据库并执行 119 迁移。
2. 部署兼容旧客户端的后端。
3. 验证旧请求体仅含 `client_request_id` 仍可发送。
4. 部署前端草稿事务和 `Idempotency-Key` Header。
5. 观察重放、冲突、卡死恢复和重复任务指标。

回滚顺序：

1. 回滚前端，恢复旧提交行为。
2. 回滚后端接入；新表保留不影响旧代码。
3. 确认无新代码访问后执行 rollback 119 删除 RPC、索引和表。

## 16. 可观测性

结构化日志统一包含：

```text
user_id, org_id, conversation_id, client_request_id,
client_task_id, assistant_message_id, fingerprint, disposition
```

建议指标：

- `message_idempotency_claim_total`
- `message_idempotency_replay_total`
- `message_idempotency_conflict_total`
- `message_send_uncertain_total`
- `message_idempotency_stale_processing_total`

告警重点：重复 key 不同 fingerprint、processing 卡死、相同用户短时间大量冲突。

## 17. 依赖与文档

- 不新增前端或后端第三方依赖。
- 使用 Python 标准库 `hashlib` 计算 SHA-256。
- 新文件需要同步 `PROJECT_OVERVIEW.md`。
- 新增函数和类型需要同步 `FUNCTION_INDEX.md`。
- 实施状态需要同步 `CURRENT_ISSUES.md`。

## 18. 实施前确认点

1. 幂等有效期采用 24 小时。
2. 同 key 并发请求采用 409 + `Retry-After`，不阻塞等待首请求。
3. 兼容期同时支持 Header 和 body 中的幂等键。
4. 第一版最多自动重试 2 次，不无限重试。
5. 新增专用表和 RPC，不把幂等状态塞入消息表。
