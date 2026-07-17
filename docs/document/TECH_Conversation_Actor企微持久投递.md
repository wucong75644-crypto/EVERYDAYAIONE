# Conversation Actor 企业微信持久投递

> 状态：阶段 1.1-1.4 已实现，待部署验收
> 日期：2026-07-17

## 1. 架构现状

Web 与企微已经共享通道无关 Chat 执行内核，Conversation Actor 使用 PostgreSQL
队列、租约和 fencing token 保证生成执行权。企微支持类型可进入持久 Actor；
FILE 固定进入 Actor，不再经过同步生成链路。生成结果由数据库 Outbox 持久投递，
不依赖短生命周期 `req_id`。

可直接复用：

- `ConversationWorker`、`ConversationExecutionService` 和原子终态 RPC。
- `ChatGenerationExecutor` 和固定 `ContextSnapshot`。
- `WecomWSClient.send_proactive` 与自建应用发送 API。
- `conversations.chat_settings` 持久化模型和思考设置。
- Redis 作为 best-effort 唤醒，不承担事实队列。

约束：

- Web 与企微必须进入同一个 conversation serial queue。
- 生成结果提交与投递任务创建必须在同一数据库事务。
- delivery context 不保存 Secret。
- 企微主动发送无可靠业务幂等 ACK，只能保证数据库结果一次提交、投递不丢和
  at-least-once。

## 2. 推荐架构

采用 PostgreSQL Transactional Outbox：

1. 企微入站按 `org/corp/msgid` 派生稳定 UUID。
2. 单事务创建输入消息、助手占位和 Actor task；重复回调返回已有 task。
3. Actor Worker 按 conversation 串行生成。
4. task 进入终态时，同事务写入 `conversation_deliveries`。
5. `wecom_ws_runner` 独立认领 delivery，通过主动消息发送。
6. 成功标记 delivered；失败指数退避，超过上限进入 dead。
7. Redis 只发送投递唤醒，Redis 不可用时数据库轮询兜底。

Redis Stream 方案不采用：它会引入 PostgreSQL 与 Redis 双写窗口，仍需额外
状态存储和补偿。

## 3. 数据库设计

`conversation_deliveries` 同时承载企微 AI 终态和 Web 用户输入镜像：

| 字段 | 说明 |
|---|---|
| id | delivery UUID |
| task_id | 关联 Actor task |
| channel | 当前为 wecom |
| delivery_kind | `assistant_terminal` 或 `web_user_message` |
| target_context | org/chatid/chattype/userid/transport，不含 Secret |
| status | pending/delivering/delivered/dead |
| attempt_count | 投递次数 |
| next_attempt_at | 下次重试时间 |
| lease_token / lease_expires_at | 投递执行权 |
| delivered_items | 已发送分项检查点 |
| last_error | 最近错误 |
| delivered_at | 完成时间 |
| created_at / updated_at | 审计时间 |

唯一约束为 `(task_id, channel, delivery_kind)`。claim 使用
`FOR UPDATE SKIP LOCKED`。
数据库触发器在 Actor task 进入 completed/failed 时插入 Outbox，保证与业务终态
同事务提交。Web task 入队时，另一个触发器仅从同会话最近一次、且与 channel
binding 一致的真实企微 task 复制目标上下文，移除旧 stream 字段后写入用户消息
镜像；无真实目标时不创建，不推导地址。

新增 RPC：

- `enqueue_wecom_generation_turn`
- `claim_conversation_delivery`
- `renew_conversation_delivery`
- `complete_conversation_delivery`
- `fail_conversation_delivery`

## 4. 边界策略

| 场景 | 策略 |
|---|---|
| 相同 msgid 重放 | 稳定 UUID + 原子 enqueue |
| 连续消息 | conversation serial queue |
| Web/企微同时发送 | 共享同一数据库 owner |
| Web 输入镜像企微 | 带“来自 Web”来源标识的主动消息，不冒充企微用户 |
| 找不到真实企微目标 | 不创建镜像，Web 入队与 AI 执行不受影响 |
| 历史企微 stream 已结束 | 镜像上下文移除 stream 字段，不覆盖旧消息 |
| Redis 中断 | 数据库轮询 |
| 企微 WS 断线 | delivery 保持 pending 并重试 |
| Worker 崩溃 | lease 过期后重新认领 |
| 发送成功、检查点前崩溃 | 接受极小概率重复；不允许丢失 |
| 文本/媒体部分成功 | 按 item 保存检查点 |
| 凭证缺失 | 重试后进入 dead 并告警 |
| 进程重启 | 设置从 conversations.chat_settings 恢复 |
| 同 msgid FILE 重放 | 复用稳定 Workspace 文件，不依赖已过期企微 URL |
| 未知文件格式 | 保留原始二进制 FilePart，由工具链处理或询问用户 |

## 5. 影响范围

新增：

- `backend/migrations/124_conversation_delivery_outbox.sql`
- `backend/migrations/rollback/124_conversation_delivery_outbox_rollback.sql`
- `backend/migrations/126_wecom_conversation_settings.sql`
- `backend/migrations/rollback/126_wecom_conversation_settings_rollback.sql`
- `backend/services/wecom/actor_enqueue.py`
- `backend/services/wecom/conversation_settings.py`
- `backend/services/wecom/delivery_worker.py`
- `backend/services/wecom/delivery_sender.py`

修改：

- `backend/services/wecom/wecom_message_service.py`
- `backend/services/conversation_runtime.py`
- `backend/services/conversation_delivery.py`
- `backend/wecom_ws_runner.py`
- `backend/services/wecom/ws_client.py`
- `backend/services/wecom/card_event_handler.py`
- `backend/services/wecom/command_handler.py`
- `backend/core/config.py`
- 相关测试与项目文档

`wecom_message_service.py` 当前超过 500 行，本阶段把生成编排拆出，修改后必须降到
500 行以内。

## 6. 开关、部署与回滚

- 该阶段曾使用 `CONVERSATION_ACTOR_WECOM_ENABLED` 灰度文本、语音、图片和混合
  消息；会话与附件治理阶段 2.2 已删除此双轨开关，上述类型统一进入 Actor。
- FILE 不保留旧链路兼容，先进入附件状态机，由下一条指令原子消费后进入 Actor。
- 必须先应用 120-126 迁移、启动 delivery consumer 和 Actor Worker，再部署包含
  FILE 新入口的应用版本；不能先发布应用再补基础设施。
- 回滚先关闭企微入队，等待 generation/delivery 排空，再停止 consumer。
- 未确认无 pending/delivering 前不执行 rollback SQL。

## 7. 验收

- 并发重复回调只产生一组消息和一个 task。
- 同 conversation 企微与 Web 不会并行执行 serial task。
- Redis、企微 WS、Actor Worker 任一重启均不丢生成结果。
- delivery token 失效后旧 Worker 不能更新状态。
- 重试、dead、分项检查点和告警均有测试。
- 新模块覆盖率不低于 80%，后端全量回归通过。

## 8. 阶段 1.3 实现结果

- `ActorPersistenceSink` 为企微任务保存恢复进度，不向 Web 推送过程事件。
- task 终态触发器在同一事务创建 Outbox；终态 observer 仅释放任务槽。
- `wecom_ws_runner` 持有智能机器人连接并运行独立 Outbox consumer。
- consumer 在长发送期间续租，每个稳定 item 成功后立即提交检查点。
- 自建应用凭证由 `AsyncOrgConfigResolver` 按企业即时解析，Outbox 不保存 Secret。
- Outbox 适配器同时检查主动发送返回值与发送后连接状态；企微无业务幂等 ACK，
  交付语义明确为 at-least-once。

## 9. 阶段 1.4 实现结果

- 删除进程内 `_session_settings`；`conversations.model_id` 与
  `chat_settings.thinking_mode` 是唯一事实源。
- 设置更新通过行锁 RPC 完成，并校验 conversation、user、org、source，避免
  JSONB 读改写覆盖和跨租户修改。
- FILE 原始字节以 msgid 派生稳定 Workspace 路径，使用临时文件加原子替换落盘，
  再同步 OSS，消息仅保存标准 `FilePart`。
- 不扫描文件、不把提取文本拼入 prompt、不按扩展名拒绝未知格式；模型工具链按需
  读取资产。
