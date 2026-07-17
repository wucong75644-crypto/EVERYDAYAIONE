# 企微会话与附件上下文治理

> 状态：已确认
> 日期：2026-07-17

## 1. 目标

修复企微 FILE 回调字段缺失、跨会话复用、附件与 Workspace 混淆、企微新旧生成双轨和工具无进展循环。私聊维持单会话连续上下文；群聊使用 `org + corp + chatid` 的共享上下文，但禁止自动注入个人记忆和个人 Workspace。

## 2. 核心数据流

- 私聊：回调规范化 → 渠道会话绑定 → FILE 暂存为活动附件 → 后续文字原子引用附件 → Actor 串行执行 → Outbox 投递。
- 群聊：按 chatid 获取共享 conversation → 保存实际 sender_user_id → 使用群资产空间 → Actor 串行执行。
- 模糊附件指代只解析当前消息和当前会话附件；只有明确要求搜索历史资料时才开放 Workspace。

## 3. 数据库设计

### 3.1 `conversation_channel_bindings`（迁移 128）

字段：`id`、`org_id`、`conversation_id`、`channel`、`corp_id`、`external_chat_id`、`chat_type`、`owner_user_id`、`created_at`、`last_seen_at`。

唯一约束：`org_id + channel + corp_id + external_chat_id`。

conversation 增加 `scope_type=user/channel` 与 `scope_id`。私聊保留用户 owner；群聊使用 channel scope。

### 3.2 消息发送者与附件（迁移 129）

messages 增加 `sender_user_id`、`sender_channel_identity`。

新增 `conversation_attachment_refs`，包含：

- 身份：`id(asset_id)`、`org_id`、`conversation_id`、`source_message_id`、`source_provider_id`
- 权限：`sender_user_id`、`storage_scope`
- 文件：`original_name`、`workspace_path`、`mime_type`、`size`
- 状态：`status`、`reference_state`、`error_code`
- 时间：`created_at`、`ready_at`、`expires_at`

资产状态：`receiving → stored → ready`，失败进入 `failed/orphan`。引用状态：`active → referenced → replaced/expired`。企微 msgid 在租户和渠道范围内唯一。

## 4. 原子边界

- `resolve_wecom_conversation`：并发安全解析/创建私聊或群聊渠道绑定。
- `stage_wecom_attachment`：幂等创建附件消息和引用，不启动无指令模型。
- 文字入队 RPC：在同一事务中解析当前会话活动附件、冻结输入消息附件快照并创建 Actor task。
- PostgreSQL 是事实源；Redis 和进程缓存只负责唤醒或加速。

## 5. 协议规范化

新增 `services/wecom/message_normalizer.py`，作为原始回调进入业务层的唯一入口：

- 私聊缺少 chatid 时回退 `from.userid`
- 群聊缺少 chatid 时拒绝
- 按真实 FILE 回调解析 filename/url/aeskey
- 校验 msgid、userid、corp、chat type
- 不记录下载凭证，不允许空 chatid 覆盖有效映射

## 6. 附件与权限

解析顺序：当前消息附件 → 当前 conversation 最近活动附件 → 显式指定的会话历史附件 → `needs_input`。

群聊附件保存到组织渠道空间；群聊禁用个人记忆和个人 Workspace。积分和敏感操作按当前发送者校验。文件名仅用于展示，工具使用 asset_id。

## 7. Actor 与工具

- FILE 暂存并回复“文件已收到，请告诉我需要如何处理”。
- TEXT/VOICE/IMAGE/MIXED 全部进入 Actor，删除企微旧同步生成链。
- `file_search` 作用域为 attachment/conversation/channel/workspace，默认禁止全 Workspace。
- 相同工具参数重复、连续文件不存在、沙盒不可用或无进展时熔断。
- 附件缺失使用 `needs_input`，不能输出技术 completed 但业务未完成的总结。

## 8. 边界场景

- 重复 msgid 返回已有结果；文件已落盘但绑定失败时进入 orphan 并可回收。
- 文件接收与后续文字通过数据库状态和 Actor 顺序消除竞态。
- 连续上传多个文件形成同一活动组；新组替换旧组；同名文件用 asset_id 区分。
- 历史私聊绑定只回填可证明数据，不猜测群聊绑定。
- 服务重启后从数据库附件状态恢复。

## 9. 实施顺序

1. 回调协议规范化与真实 FILE 契约测试。
2. 渠道会话绑定和私聊回填。
3. 群共享 conversation 与消息发送者。
4. 附件状态机和原子暂存。
5. 文字原子引用附件。
6. 企微全量 Actor 化并删除旧链。
7. Workspace、记忆、缓存作用域。
8. 工具熔断与 needs_input。
9. 全量回归、迁移、部署和生产端到端验证。

## 10. 部署与回滚

先应用只新增结构的迁移，再回填、双读验证、切换 Actor，最后删除旧路径。回滚时先关闭新入口并恢复代码；新表停止写入但保留数据，确认完全回滚后才执行 rollback SQL。无新增第三方依赖。
## 11. 阶段 3.1 落地补充：ExecutionScope

生成执行使用数据库会话记录解析 `ExecutionScope`，不接受客户端直接指定资源作用域：

- `actor_user_id`：真实发言人，负责积分、审计和组织权限。
- `context_scope`：私聊为 `user`，群聊为 `channel`。
- `workspace_owner_id`：私聊为用户 ID；群聊为服务端计算的稳定 channel owner。
- `personal_context_allowed`：群聊固定为 `false`。

群聊继续使用冻结的 ContextSnapshot 和会话摘要，但不读取个人 Memory、persona、
偏好或位置，也不开放个人积分、记忆、新对话、定时任务及重复的
`get_conversation_context` 工具。文件分析、Sandbox、ERP 导出与媒体生成均使用
channel Workspace；扣费和操作审计仍使用真实发言人。
