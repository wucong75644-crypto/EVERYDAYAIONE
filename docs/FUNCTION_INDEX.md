# 函数索引 (FUNCTION_INDEX)

> 本文档记录项目中所有函数的索引信息，包括函数名、文件路径、功能描述等。

## 更新规则
- 新增函数时必须同步更新本文档
- 修改函数签名时必须更新对应条目
- 删除函数时必须从本文档移除

## 函数列表

### 通用 Curated Memory 数据库协议

| 函数名 | 文件路径 | 功能描述 | 参数 | 返回值 |
|--------|----------|----------|------|--------|
| `create_manual_memory` | `backend/migrations/144_manual_curated_memory.sql` | 在个人或组织 scope 内串行执行容量检查与内容哈希去重，创建用户确认的手动 Curated Memory | org_id, user_id, content, content_hash, embedding, priority | JSONB outcome |
| `update_manual_memory` | `backend/migrations/144_manual_curated_memory.sql` | 仅更新当前 scope 内的手动记忆，并同步重建向量、全文索引和内容哈希 | org_id, user_id, memory_id, content, content_hash, embedding | JSONB outcome |
| `delete_memory_atom` | `backend/migrations/144_manual_curated_memory.sql` | 软删除当前 scope 内指定的 active Curated Memory | org_id, user_id, memory_id | JSONB outcome |
| `clear_memory_atoms` | `backend/migrations/144_manual_curated_memory.sql` | 软删除当前 scope 内全部 active Curated Memory | org_id, user_id | JSONB outcome |
| `ManualMemoryService.get_all_memories` | `backend/services/memory/manual_memory_service.py` | NULL-safe 列出当前用户 scope 内最多 100 条 active Curated Memory | user_id, org_id? | list[dict] |
| `ManualMemoryService.get_memory_count` | `backend/services/memory/manual_memory_service.py` | 统计当前用户 scope 内可管理的 active Curated Memory | user_id, org_id? | int |
| `ManualMemoryService.add_memory` | `backend/services/memory/manual_memory_service.py` | 原文归一化、生成 embedding 并通过原子 RPC 创建或复用手动记忆 | user_id, content, source, org_id? | list[dict] |
| `ManualMemoryService.update_memory` | `backend/services/memory/manual_memory_service.py` | 校验 UUID、生成新 embedding 并仅更新当前 scope 的 manual 记忆 | memory_id, content, user_id, org_id? | dict |
| `ManualMemoryService.delete_memory` | `backend/services/memory/manual_memory_service.py` | 通过原子 RPC 软删除当前 scope 的指定记忆 | memory_id, user_id, org_id? | None |
| `ManualMemoryService.delete_all_memories` | `backend/services/memory/manual_memory_service.py` | 通过原子 RPC 软删除当前 scope 的全部 active 记忆 | user_id, org_id? | None |
| `get_memory_service` | `backend/api/routes/memory.py` | 为 Web 记忆设置与 CRUD 路由创建 scoped `ManualMemoryService` | db | ManualMemoryService |
| `MemorySettingsService.is_memory_enabled` | `backend/services/memory_settings.py` | 仅依据用户设置判断通用记忆是否开启，不依赖旧 Mem0 Provider | user_id | bool |

### 前端消息运行时协议与展示安全

| 函数名 | 文件路径 | 功能描述 | 参数 | 返回值 |
|--------|----------|----------|------|--------|
| `parseProtocolString` | `frontend/src/schemas/messageProtocol.ts` | 校验 WebSocket/恢复链路中的字符串字段，拒绝对象隐式转换 | input, field, context? | `string \| null` |
| `parseContentPart` | `frontend/src/schemas/messageProtocol.ts` | 在 Store 写入前校验单个 ContentPart；兼容恢复结构化 text | input, context? | `ContentPart \| null` |
| `parseContentParts` | `frontend/src/schemas/messageProtocol.ts` | 校验内容块数组并隔离非法块 | input, context? | `ContentPart[]` |
| `DiagramPart.validate_source` | `backend/schemas/diagram.py` | 校验 Mermaid 结构化消息源码非空且不超过协议上限 | value | `str` |
| `ChartPart.normalize_spec_format` | `backend/schemas/chart.py` | 保留三种已知图表格式并将未知历史格式归一化为可读降级类型 | value | `str` |
| `MermaidRenderer` | `frontend/src/components/chat/message/MermaidRenderer.tsx` | 按需加载 Mermaid、安全清理 SVG，并处理缓存、竞态、重试和源码降级 | source, messageId? | React element |
| `DiagramBlock` | `frontend/src/components/chat/message/DiagramBlock.tsx` | 结构化 DiagramPart 正式展示入口，复制操作始终使用原始 Mermaid 源码 | diagram, messageId | React element |
| `EChartsRenderer` | `frontend/src/components/chat/message/EChartsRenderer.tsx` | 按需加载 ECharts并处理主题、统一状态、重试、脱敏错误日志和 JSON/表格降级 | option, title?, messageId? | React element |
| `useEChartsRender` | `frontend/src/components/chat/message/useEChartsRender.ts` | 管理 ECharts 异步 Chunk、实例初始化、错误重试、卸载清理和响应式尺寸监听 | containerRef, chartRef, option, theme, messageId? | render state |
| `echartsRuntime.init` | `frontend/src/components/chat/message/echartsRuntime.ts` | 在独立动态边界内具名注册受支持图表、组件、Canvas 渲染器与主题，并导出 ECharts 初始化函数 | DOM element, theme? | ECharts instance |
| `PdfPreview` | `frontend/src/preview/adapters/PdfPreview.tsx` | 功能触发后加载 PDF.js，渲染 PDF 并管理翻页、缩放、进度与错误状态 | PreviewCommonProps | React element |
| `PdfPreviewControls` | `frontend/src/preview/adapters/PdfPreviewControls.tsx` | PDF 与 Office 预览共用的翻页、页码和缩放控制栏 | control props | React element |
| `PptxPreview` | `frontend/src/preview/adapters/PptxPreview.tsx` | 功能触发后转换 Office 文档并使用 PDF.js 渲染，包含取消与 Blob URL 清理 | PreviewCommonProps | React element |
| `formatDisplayValue` | `frontend/src/utils/displayValue.ts` | 将未知值稳定转换为可展示文本，处理 BigInt 与循环引用 | value | `string` |
| `formatFormValue` | `frontend/src/utils/displayValue.ts` | 仅允许标量进入表单控件，拒绝结构化值 | value | `string` |
| `createStreamingLifecycleActions` | `frontend/src/stores/slices/streamingLifecycleActions.ts` | 创建流式消息启动、注册、完成和查询 actions | set, get | lifecycle actions |
| `createOptimisticMessageActions` | `frontend/src/stores/slices/optimisticMessageActions.ts` | 创建乐观消息幂等写入、替换、移除 actions | set, get | optimistic actions |
| `createStreamingUiActions` | `frontend/src/stores/slices/streamingUiActions.ts` | 创建思考、步骤提示、建议和工具确认 actions | set, get | UI actions |
| `handleRoutingComplete` | `frontend/src/contexts/wsRoutingCompleteHandler.ts` | 处理模型路由完成后的媒体占位符或聊天参数更新 | deps, msg | void |
| `parseSpreadsheetCsv` | `frontend/src/preview/adapters/spreadsheetData.ts` | 解析支持引号、换行和自定义分隔符的表格文本 | text, separator | `string[][]` |
| `clearMergedCells` | `frontend/src/preview/adapters/spreadsheetData.ts` | 清除 Excel 合并区域中非左上角重复单元格 | worksheet | void |

### 任务管理模块 (Task Management)

#### 后端函数

| 函数名 | 文件路径 | 功能描述 | 参数 | 返回值 |
|--------|----------|----------|------|--------|
| `create_task` | `backend/services/task_service.py` | 创建新任务并加入队列 | user_id, conversation_id, prompt, model_config | Task对象 |
| `get_active_tasks` | `backend/services/task_service.py` | 获取用户所有活跃任务 | user_id | List[Task] |
| `count_active_tasks` | `backend/services/task_service.py` | 统计用户全局活跃任务数 | user_id | int |
| `count_conversation_active_tasks` | `backend/services/task_service.py` | 统计单对话活跃任务数 | conversation_id | int |
| `update_task_status` | `backend/services/task_service.py` | 更新任务状态和进度 | task_id, status, progress, result | Task对象 |
| `handle_task_completion` | `backend/services/task_service.py` | 处理任务完成（扣除积分、通知前端） | task_id, result | bool |
| `handle_task_failure` | `backend/services/task_service.py` | 处理任务失败（退回积分、通知前端） | task_id, error | bool |
| `call_ai_api` | `backend/services/ai_service.py` | 调用AI API生成内容（含重试） | prompt, model, timeout | Dict |
| `process_task_worker` | `backend/workers/task_worker.py` | 任务队列Worker处理函数 | task_id | None |
| `BackgroundTaskWorker.poll_pending_tasks` | `backend/services/background_task_worker.py` | 轮询 pending/running 的 image/video 任务（兜底模式，120s 间隔） | - | None |
| `BackgroundTaskWorker.query_and_process` | `backend/services/background_task_worker.py` | 查询 Provider 任务状态，完成/失败交给 TaskCompletionService | task: dict | None |
| `BackgroundTaskWorker.cleanup_stale_tasks` | `backend/services/background_task_worker.py` | 清理超时任务（image/video 走 TaskCompletionService，chat 直接更新） | - | None |

#### 前端函数

| 函数名 | 文件路径 | 功能描述 | 参数 | 返回值 |
|--------|----------|----------|------|--------|
| `useMessageStore` | `frontend/src/stores/useMessageStore.ts` | Zustand 统一消息状态管理 | - | MessageStore |
| `registerSessionStoreReset` | `frontend/src/stores/sessionStoreResetRegistry.ts` | 按稳定 Store 名注册同步会话清理回调，重复注册时覆盖旧回调 | name, reset | void |
| `resetSessionStores` | `frontend/src/stores/sessionStoreResetRegistry.ts` | 同步清理当前已加载的消息、记忆与订阅 Store，避免认证 Store 静态依赖业务 Store | - | void |
| `submitTask` | `frontend/services/taskService.ts` | 提交新任务到后端 | conversationId, prompt, modelConfig | Promise<Task> |
| `checkTaskLimits` | `frontend/services/taskService.ts` | 检查任务数量限制 | conversationId | boolean |
| `subscribeTaskUpdates` | `frontend/services/websocket.ts` | 订阅任务实时更新 | taskIds | void |
| `handleTaskProgress` | `frontend/services/websocket.ts` | 处理任务进度推送 | event | void |
| `handleTaskCompleted` | `frontend/services/websocket.ts` | 处理任务完成推送 | event | void |
| `getConversationTaskBadge` | `frontend/utils/taskUtils.ts` | 计算对话任务徽章数量 | conversationId | {processing: number, completed: number} |
| `mergeTasks` | `frontend/stores/taskStore.ts` | 合并任务列表（断线重连用） | tasks | void |

### Redis 基础设施模块 (Redis Infrastructure)

#### 后端函数

| 函数名 | 文件路径 | 功能描述 | 参数 | 返回值 |
|--------|----------|----------|------|--------|
| `RedisClient.get_client` | `backend/core/redis.py` | 获取 Redis 客户端（单例模式） | - | Redis |
| `RedisClient.close` | `backend/core/redis.py` | 关闭 Redis 连接 | - | None |
| `RedisClient.health_check` | `backend/core/redis.py` | Redis 健康检查 | - | bool |
| `RedisClient.acquire_lock` | `backend/core/redis.py` | 获取分布式锁 | key, timeout | Optional[str] |
| `RedisClient.release_lock` | `backend/core/redis.py` | 释放分布式锁（Lua 原子操作） | key, token | bool |
| `RedisClient.extend_lock` | `backend/core/redis.py` | 延长锁的过期时间 | key, token, timeout | bool |

### 任务限制服务模块 (Task Limit Service)

#### 后端函数

| 函数名 | 文件路径 | 功能描述 | 参数 | 返回值 |
|--------|----------|----------|------|--------|
| `check_and_acquire` | `backend/services/task_limit_service.py` | 检查限制并获取槽位 | user_id, conversation_id | bool |
| `release` | `backend/services/task_limit_service.py` | 释放任务槽位 | user_id, conversation_id | None |
| `get_active_count` | `backend/services/task_limit_service.py` | 获取活跃任务数量 | user_id, conversation_id? | dict |
| `can_start_task` | `backend/services/task_limit_service.py` | 检查是否可以启动新任务 | user_id, conversation_id | bool |

### 积分管理模块 (Credits Management)

#### 后端函数

| 函数名 | 文件路径 | 功能描述 | 参数 | 返回值 |
|--------|----------|----------|------|--------|
| `get_balance` | `backend/services/credit_service.py` | 获取用户积分余额 | user_id | int |
| `deduct_atomic` | `backend/services/credit_service.py` | 原子扣除积分（RPC 保证原子性） | user_id, amount, reason, change_type | int |
| `lock_credits` | `backend/services/credit_service.py` | 预扣积分（锁定） | task_id, user_id, amount, reason | str |
| `confirm_deduct` | `backend/services/credit_service.py` | 确认扣除（任务成功时调用） | transaction_id | None |
| `refund_credits` | `backend/services/credit_service.py` | 退回积分（任务失败时调用） | transaction_id | None |
| `credit_lock` | `backend/services/credit_service.py` | 积分锁定上下文管理器 | task_id, user_id, amount, reason | AsyncContextManager |
| `get_credit_service` | `backend/services/credit_service.py` | 获取积分服务实例（依赖注入） | db, redis? | CreditService |
| `admin_adjust_credits` | `backend/api/routes/admin_users_helpers.py` | 管理员手动调整积分（正=充值/负=扣减），调用 RPC，写 operator_id 审计 | db, user_id, delta, reason, operator_id, org_id? | int (新余额) |

### 管理员用户管理模块 (Admin Users)

#### 后端函数（全部需 super_admin）

| 函数名 | 文件路径 | 功能描述 | 参数 | 返回值 |
|--------|----------|----------|------|--------|
| `list_users` | `backend/api/routes/admin_users.py` | 用户列表（搜手机号/昵称 + 分页 + org 过滤 + phone 脱敏） | search?, org_id?, page, page_size | dict |
| `get_user_summary` | `backend/api/routes/admin_users.py` | 用户概览（余额/累计消耗/对话数/所属企业） | uid | dict |
| `recharge_credits` | `backend/api/routes/admin_users.py` | 充值/扣减积分（写 admin_action_logs 审计） | uid, delta, reason?, org_id? | dict |
| `get_credits_history` | `backend/api/routes/admin_users.py` | 积分流水（带 operator 昵称） | uid, page, page_size | dict |
| `list_user_conversations` | `backend/api/routes/admin_users.py` | 用户对话列表 | uid, page, page_size | dict |
| `get_conversation_messages` | `backend/api/routes/admin_users.py` | 对话内消息（含 content JSONB 解析的附件） | uid, cid, limit? | dict |
| `download_user_assets_zip` | `backend/api/routes/admin_users_zip.py` | 按资产 ID 复验用户归属、ready 状态和 HTTPS CDN/OSS 主机后流式打包 ZIP | uid, asset_ids | StreamingResponse |
| `_require_super_admin` | `backend/api/routes/admin_users_helpers.py` | super_admin 权限校验依赖 | user_id, db | None |
| `_safe_parse_content` | `backend/api/routes/admin_users_helpers.py` | messages.content JSONB 容错解析 | raw | Any |
| `_extract_upload_parts` | `backend/api/routes/admin_users_helpers.py` | 从 content 数组提取 file/image/image_url ContentPart | parts | list[dict] |
| `_log_admin_action` | `backend/api/routes/admin_users_helpers.py` | 写 admin_action_logs（失败不阻断） | db, admin_id, action_type, ... | None |

#### 前端函数

| 函数名 | 文件路径 | 功能描述 | 参数 | 返回值 |
|--------|----------|----------|------|--------|
| `listAdminUsers` | `frontend/src/services/adminUser.ts` | 用户列表 API 封装 | { search?, org_id?, page?, page_size? } | Promise<AdminUserListResponse> |
| `getAdminUserSummary` | `frontend/src/services/adminUser.ts` | 用户概览 API | uid | Promise<AdminUserSummary> |
| `rechargeUserCredits` | `frontend/src/services/adminUser.ts` | 充值/扣减 API | uid, { delta, reason?, org_id? } | Promise<RechargeResponse> |
| `getUserCreditsHistory` | `frontend/src/services/adminUser.ts` | 流水 API | uid, { page?, page_size? } | Promise<CreditsHistoryResponse> |
| `listUserConversations` | `frontend/src/services/adminUser.ts` | 对话列表 API | uid, { page?, page_size? } | Promise<ConversationListResponse> |
| `getUserConversationMessages` | `frontend/src/services/adminUser.ts` | 对话消息 API | uid, cid, { limit? } | Promise<ConversationMessagesResponse> |
| `listUserAssets` | `frontend/src/services/adminUser.ts` | 统一用户资产列表，支持来源/媒体筛选、复合游标和请求取消 | uid, { source_type, media_type?, limit?, cursor? }, signal? | Promise<UserAssetsResponse> |
| `downloadUserAssetsZip` | `frontend/src/services/adminUser.ts` | 提交资产 ID 并触发安全批量 ZIP 下载（fetch + Blob） | uid, assetIds | Promise<void> |
| `formatRelativeCN` | `frontend/src/utils/formatRelativeCN.ts` | 相对时间中文格式化 | iso | string |
| `UserManagePanel` | `frontend/src/components/admin/UserManagePanel.tsx` | 用户列表 + 搜索 + 分页 + 详情抽屉 | — | JSX |
| `UserDetailDrawer` | `frontend/src/components/admin/UserDetailDrawer.tsx` | 右侧滑入抽屉，含 3 Tab | userId, onClose, onChanged? | JSX |
| `CreditsTab` | `frontend/src/components/admin/userDetail/CreditsTab.tsx` | 余额 + 充值表单（二次确认）+ 流水 | userId, balance, status?, onChanged | JSX |
| `ConversationViewTab` | `frontend/src/components/admin/userDetail/ConversationViewTab.tsx` | 左对话列表 + 右消息流，保留单素材下载，不再向服务端提交 URL 批量打包 | userId | JSX |
| `AssetSpaceTab` | `frontend/src/components/admin/userDetail/AssetSpaceTab.tsx` | 统一资产 [上传/生成] 切换 + 复合游标分页 + 资产 ID 多选下载 | userId | JSX |

### 对话管理模块 (Conversation Management)

#### 后端函数

| 函数名 | 文件路径 | 功能描述 | 参数 | 返回值 |
|--------|----------|----------|------|--------|
| `create_conversation` | `backend/services/conversation_service.py` | 创建新对话 | user_id | Conversation对象 |
| `update_conversation_title` | `backend/services/conversation_service.py` | 更新对话标题 | conversation_id, title, is_custom | bool |
| `generate_auto_title` | `backend/services/conversation_service.py` | 自动生成对话标题（基于首条消息） | first_message | str |
| `get_conversation_list` | `backend/services/conversation_service.py` | 获取用户对话列表（按时间分组） | user_id | List[Conversation] |
| `delete_conversation` | `backend/services/conversation_service.py` | 删除对话及相关消息 | conversation_id | bool |

#### 前端函数

| 函数名 | 文件路径 | 功能描述 | 参数 | 返回值 |
|--------|----------|----------|------|--------|
| `useConversationStore` | `frontend/stores/conversationStore.ts` | Zustand对话状态管理 | - | ConversationStore |
| `updateConversationTitle` | `frontend/services/conversationService.ts` | 更新对话标题并同步 | conversationId, title | Promise<bool> |
| `generateAutoTitle` | `frontend/utils/conversationUtils.ts` | 前端自动生成标题逻辑 | firstMessage | string |
| `syncTitleToNavbar` | `frontend/components/Navbar.tsx` | 同步标题到顶部导航栏 | conversationId, title | void |
| `handleTitleEdit` | `frontend/components/Navbar.tsx` | 处理导航栏标题编辑 | - | void |

### 消息处理模块 (Message Handlers)

#### 前端函数

| 函数名 | 文件路径 | 功能描述 | 参数 | 返回值 |
|--------|----------|----------|------|--------|
| `useMessageHandlers` | `frontend/src/hooks/useMessageHandlers.ts` | 消息处理器组合 Hook（使用统一 useMediaMessageHandler） | UseMessageHandlersParams | {handleChatMessage, handleImageGeneration, handleVideoGeneration} |
| `useTextMessageHandler` | `frontend/src/hooks/handlers/useTextMessageHandler.ts` | 文本消息处理 Hook | UseTextMessageHandlerParams | {handleChatMessage} |
| `useMediaMessageHandler` | `frontend/src/hooks/handlers/useMediaMessageHandler.ts` | 统一媒体消息处理 Hook（合并图片/视频） | UseMediaMessageHandlerParams | {handleMediaGeneration} |
| `extractErrorMessage` | `frontend/src/hooks/handlers/mediaHandlerUtils.ts` | 从错误对象提取友好消息 | error: unknown | string |
| `extractImageUrl` | `frontend/src/hooks/handlers/mediaHandlerUtils.ts` | 从 API 响应提取图片 URL | result: unknown | string \| undefined |
| `extractVideoUrl` | `frontend/src/hooks/handlers/mediaHandlerUtils.ts` | 从 API 响应提取视频 URL | result: unknown | string \| undefined |
| `handleGenerationError` | `frontend/src/hooks/handlers/mediaHandlerUtils.ts` | 处理生成错误并创建错误消息 | conversationId, errorPrefix, error, createdAt?, generationParams? | Promise<Message> |

### 上下文压缩模块 (Context Compression)

> **2026-05-23 重构**：原 `context_compressor.py` (773 行) 按职责拆为 4 个子模块 + `__init__.py` 重导出。所有原 `from services.handlers.context_compressor import xxx` 调用通过 `__init__.py` 向后兼容。

#### 包结构

| 子模块 | 行数 | 职责 |
|--------|------|------|
| `context_compressor/__init__.py` | 78 | 重导出所有公共符号（向后兼容） |
| `context_compressor/tokens.py` | 79 | token 估算 + 文本提取 + 归档判断 + system prompt 去重 |
| `context_compressor/archive.py` | 290 | 层4 工具结果归档（含轮次识别） |
| `context_compressor/budget.py` | 277 | 层6 Token 预算管理（整体/工具桶/历史桶） |
| `agent/runtime/context/compaction.py` | 252 | 当前 Run 唯一 LLM Compaction 合同与无正文回执 |

#### 后端函数

| 函数名 | 文件路径 | 功能描述 | 参数 | 返回值 |
|--------|----------|----------|------|--------|
| `estimate_tokens` | `backend/services/handlers/context_compressor/tokens.py` | 估算 messages 列表的总 token 数（基于字符数偏保守） | messages: List[Dict] | int |
| `compact_stale_tool_results` | `backend/services/handlers/context_compressor/archive.py` | 层4 工具结果归档：按工具轮次保留最近 N 轮（**企微链路用**） | messages, keep_turns=2 | int |
| `_identify_tool_turns` | `backend/services/handlers/context_compressor/archive.py` | 按 `assistant+tool_calls` 切分工具轮次（企微用） | messages | List[List[int]] |
| `_identify_user_turns` | `backend/services/handlers/context_compressor/archive.py` | 按 `role=user` 切分用户对话回合（**Web 用**） | messages | List[Tuple[int,int]] |
| `compact_stale_by_user_turns` | `backend/services/handlers/context_compressor/archive.py` | Web 端工具结果归档：按用户对话回合 + 容量触发 | messages, keep_user_turns=10, capacity_trigger=0.7, max_tokens=200000 | int |
| `compact_context` | `backend/services/agent/runtime/context/compaction.py` | 当前 Run 唯一 LLM Compaction 合同：85% 触发、相同 prefix single-flight/失败 suppression、应用前指纹复核，并返回无正文 CompactionReceipt | messages, usable_input, trigger_ratio=0.85, suppression_scope?, model_step=0 | CompactionReceipt |
| `compaction_prefix_fingerprint` | `backend/services/agent/runtime/context/compaction_guard.py` | 对实际待替换消息稳定序列化并生成 SHA-256，不存储正文 | messages, indices | str |
| `acquire_loop_compaction/finish_loop_compaction` | `backend/services/agent/runtime/context/compaction_guard.py` | 当前 Run 相同 prefix 的进程内 single-flight 与失败 suppression；状态有界保存 | scope, prefix_fingerprint | str / None |
| `clear_loop_compaction_scope` | `backend/services/agent/runtime/context/compaction_guard.py` | Run 结束时清除对应 task scope 的 suppression 与 in-flight 状态 | scope | None |
| `enforce_tool_budget` | `backend/services/handlers/context_compressor/budget.py` | 工具结果桶：超预算从最旧 tool 开始归档 | messages, max_tokens | None |
| `enforce_history_budget_sync` | `backend/services/handlers/context_compressor/budget.py` | 历史消息桶：超预算反向累积找切点 | messages, max_tokens | None |
| `ChatGenerateMixin._get_conv_source` | `backend/services/handlers/chat_generate_mixin.py` | 读取并缓存 conversations.source（Web/企微分流用） | conversation_id | str |
| `ChatGenerateMixin.generate_complete` | `backend/services/handlers/chat_generate_mixin.py` | 企微非流式兼容门面；调用统一 Chat 执行内核并把执行异常转换为通道友好提示 | content, user_id, conversation_id, model_id, context_anchor | GenerateResult |
| `execute_chat` | `backend/services/handlers/chat/execution_engine.py` | 通道无关模型流、工具循环、预算、结构化产物和 usage 执行内核；不提交任务终态 | handler, request, cancellation_event?, sink? | ChatExecutionResult |
| `ChatGenerationExecutor.execute` | `backend/services/handlers/chat/executor.py` | 校验 Actor task/claim，从输入消息恢复完整 ContentPart 与固定 ContextAnchor，返回纯 GenerationOutcome | task, claim, cancellation_event | GenerationOutcome |
| `CollectingExecutionSink` | `backend/services/handlers/chat/execution_sink.py` | 无 WebSocket、无数据库副作用的文本、思考和内容块过程事件收集器 | - | ExecutionSink |
| `ActorWebSink` | `backend/services/handlers/chat/actor_sink.py` | 推送 Actor 流式过程事件；临时进度通过 execution token 条件 RPC 持久化，WS 故障不改变执行结果 | db, delivery, cancellation_event, websocket | ExecutionSink |
| `ActorTerminalDelivery.notify` | `backend/services/conversation_delivery.py` | 数据库终态确认后释放任务槽；所有通道均向 Web best-effort 推送终态，企微结果另由事务 Outbox 投递 | task, terminal_result | None |
| `enqueue_web_chat` | `backend/services/handlers/chat/actor_enqueue.py` | 以稳定内部 UUID 调用原子 enqueue RPC，并 best-effort 发布 Redis 唤醒 | handler, content, user_id, conversation_id, external_task_id, model_id, metadata, params | str |
| `stable_actor_task_id` | `backend/services/handlers/chat/actor_enqueue.py` | 按用户、对话和外部 task ID 确定性生成 Actor 内部 task UUID | user_id, conversation_id, external_task_id | str |
| `ConversationActorRuntime.start/stop` | `backend/services/conversation_runtime.py` | 装配独立 Worker、执行器、统一实时 Sink、终态观察器和 Kernel 生命周期 | - | None |
| `WecomDeliverySender.build_items/send` | `backend/services/wecom/delivery_sender.py` | 将 AI 终态或带来源标识的 Web 用户文本展开为稳定分项；chart 降级为格式化 JSON、diagram 降级为原始 Mermaid 源码，智能机器人终态可完成原 stream | task, message, context, delivery_kind / context, item | list / bool |
| `_graphic_fallback` | `backend/services/wecom/delivery_sender.py` | 将企微不支持的结构化 chart/diagram 转换为仍可读取的文本数据 | part | str / None |
| `WecomDeliveryWorker.start/stop/run_once` | `backend/services/wecom/delivery_worker.py` | 轮询认领企微事务 Outbox，按 delivery_kind 加载输入或助手消息，续租、逐项检查点、完成或指数退避/dead | - | None / bool |
| `cancel_actor_task` | `backend/services/conversation_task.py` | 经 user/org 范围约束的 cancel RPC 原子取消 Actor task | db, task, user_id, org_id | bool |
| `update_generation_progress` | `backend/migrations/123_conversation_actor_progress.sql` | 仅当前 running task 的有效 fencing token 可更新 accumulated 内容与块 | task_id, execution_token, accumulated_content, accumulated_blocks | JSONB |
| `build_running_step` | `backend/services/handlers/chat/tool_loop.py` | Web 与无头执行内核共用的 running tool_step 构造原语 | call | Dict |
| `build_block_from_payload` | `backend/services/handlers/emit_payloads.py` | 将可信 emit payload 转成流式推送与持久化使用的 content block | payload | Optional[Dict] |
| `build_diagram_payload` | `backend/services/sandbox/emit_protocol.py` | 构造 Mermaid diagram emit payload并校验格式、空值和长度 | source, title, format | dict |
| `build_part_from_payload` | `backend/services/handlers/emit_payloads.py` | 将可信 emit payload 转成非流式链路使用的 ContentPart | payload | Optional[ContentPart] |
| `context_anchor_from_binding` | `backend/services/handlers/context_snapshot.py` | 将 Turn 绑定事务结果转换为不可变任务上下文锚点 | task_data, input_message_id, turn_id, binding_data | ContextAnchor |
| `build_context_snapshot` | `backend/services/handlers/context_snapshot.py` | 校验任务输入锚点，只按 base revision 从 ConversationItem/Compaction 构造闭合历史、资源和跨 Turn 数据证据 | db, anchor, current_text | ContextSnapshot |
| `get_closed_messages` | `backend/services/handlers/conversation_cache.py` | 从隔离的 v6 key 读取 revision/through-message 精确匹配的统一历史；旧 key 自动失效，Redis 故障降级为数据库 miss | conv_id, requested_revision, through_message_id, org_id, task_id?, turn_id? | Optional[List[Dict]] |
| `set_closed_messages` | `backend/services/handlers/conversation_cache.py` | 写入 v6 schema、快照上界和闭合消息边界，不接收任务私有工具循环 | conv_id, revision, through_message_id, messages, org_id, ttl, task_id?, turn_id? | bool |
| `build_context_messages` | `backend/services/handlers/chat_context/history_loader.py` | 仅供离线消息审计和迁移测试的固定 revision 投影，不进入正式任务上下文 | db, conversation_id, current_text, base_revision?, strict? | List[Dict] |
| `_build_history_query` | `backend/services/handlers/chat_context/history_loader.py` | 构造离线消息审计的固定快照上界查询 | db, conversation_id, base_revision | query |
| `_row_to_oai_messages` | `backend/services/handlers/chat_context/history_loader.py` | 将数据库消息投影为闭合历史；近期正常 Turn 仅恢复成功、闭合、≤8KB 且无明显凭证的工具对，中断 Turn 保留恢复协议 | row, remaining_images, preserve_tool_protocol?, preserve_safe_completed_tools? | messages, image_count |
| `_extract_user_file_refs` | `backend/services/handlers/chat_context/content_extractors.py` | 提取历史用户附件的叙事性文件名，不向模型暴露 workspace_path | blocks | List[str] |
| `_project_tool_step` | `backend/services/handlers/chat_context/content_extractors.py` | 将单个持久化工具步骤投影为闭合 OAI tool pair，并执行正常历史的成功状态、大小和凭证筛选 | part, sequence_index, safe_completed_only, max_output_bytes | List[Dict] |
| `apply_context_summary` | `backend/migrations/137_context_summary_revision_rpc.sql` | 锁定 conversation 并以 expected revision CAS 提交闭合摘要，拒绝旧结果和非法消息边界 | conversation_id, expected_revision, through_revision, through_message_id, summary, message_count | JSONB |
| `_estimate_message_tokens` | `backend/services/handlers/chat_context/history_loader.py` | 估算一组标准消息 token，仅控制加载批次与日志 | messages | int |
| `_append_tool_digest` | `backend/services/handlers/chat_context/history_loader.py` | 将持久化工具摘要追加到当前 assistant 行的用户可见文本 | messages, row | None |
| `_finalize_history` | `backend/services/handlers/chat_context/history_loader.py` | 补全工具配对、中断提示和 legacy 文本去重 | context, interrupt_marker, current_text, is_legacy | List[Dict] |
| `PromptBuilder._compose_messages` | `backend/services/prompt_builder/builder.py` | 按稳定前缀顺序拼接静态层、会话层、统一历史和 Evidence，再注入动态时间、当前资源与用户输入 | static/session/turn/history/user_result | List[Dict] |
| `StreamChunk` | `backend/services/adapters/types.py` | Provider 流式响应统一结构；除输入输出 Token 外透传 cached_tokens 与 cache_creation_tokens，缺省为零 | content, usage, tool_calls | dataclass |
| `accumulate_cache_usage` | `backend/services/handlers/chat/stream_session.py` | Web 流式与 Headless 共用的 Provider 缓存命中/创建 Token 累积原语 | totals, chunk | None |
| `bind_generation_turn` | `backend/migrations/120_turn_revision_foundation.sql` | 原子绑定 task 的输入消息、Turn 和上下文基线；相同参数重复调用幂等 | conversation_id, task_id, input_message_id, turn_id, execution_mode | JSONB |
| `prepare_generation` | `backend/migrations/148_unified_generation_prepare.sql` | 原子创建或验证生成请求的 Turn、输入/输出消息与 1–16 个本地 task | request_id, operation, conversation/user/org/turn, input/output JSONB, tasks JSONB | JSONB |
| `_prepare_generation_messages` | `backend/migrations/148_unified_generation_prepare.sql` | `prepare_generation` 内部完成消息锚点创建、恢复和冲突校验 | operation, conversation/org/turn, input/output JSONB | JSONB |
| `_prepare_generation_tasks` | `backend/migrations/148_unified_generation_prepare.sql` | `prepare_generation` 内部幂等创建并校验 1–16 个本地 task | tasks JSONB, scope, message/turn/context anchor | JSONB |
| `attach_generation_external_task` | `backend/migrations/148_unified_generation_prepare.sql` | 校验最终积分交易后，把供应商任务、实际模型和请求参数幂等附加到 preparing 本地任务，并转为 pending | task_id, external_task_id, credit_transaction_id, org_id, actual_model_id, actual_request_params | JSONB |
| `fail_prepared_generation_task` | `backend/migrations/148_unified_generation_prepare.sql` | 将尚未提交供应商的 preparing task 幂等置为 failed | task_id, terminal_reason, error_message, org_id | JSONB |
| `GenerationPreparation.from_rpc` | `backend/services/generation_lifecycle.py` | 严格解析统一准备 RPC 的权威消息、Turn、revision 与 task 集合 | data | GenerationPreparation |
| `GenerationPreparation.context_anchor` | `backend/services/generation_lifecycle.py` | 为 preparation 中的指定 task 构造不可变上下文锚点 | task_id, org_id | ContextAnchor |
| `GenerationLifecycle.prepare` | `backend/services/generation_lifecycle.py` | 序列化白名单 payload 并调用统一准备 RPC；声明的关系冲突脱敏映射为 HTTP 409，未知故障保留 5xx | request/operation/scope/turn/messages/tasks | GenerationPreparation |
| `GenerationLifecycle.attach_external_task` | `backend/services/generation_lifecycle.py` | 校验并附加供应商 task，返回类型化幂等结果 | task/external/transaction/scope/provider | ExternalTaskAttachment |
| `GenerationLifecycle.fail_prepared_task` | `backend/services/generation_lifecycle.py` | 将 preparing task 置为失败并记录业务上下文 | task/reason/error/scope/user | PreparedTaskFailure |
| `prepare_and_start_ecom_generation` | `backend/api/routes/message_ecom_preparation.py` | 识别电商策划/生图阶段，并在 Handler 前原子准备消息与本地任务 | db/handler/conversation/scope/request/body | GenerateResponse |
| `EcomImageHandler.prepare_phase2_params` | `backend/services/handlers/ecom_image_handler.py` | 按营销图/白底图规则构造电商批次 prompt 和参考图 | content, params | list |
| `classify_candidate` | `backend/scripts/backfill_generation_turns.py` | 按 task/reply/同 Turn/唯一前序输入的权威顺序分类历史 assistant 关系 | candidate row | RepairPlan |
| `fetch_batch` | `backend/scripts/backfill_generation_turns.py` | 按 created_at/id keyset 读取待回填 assistant，apply 时使用维护窗口 `FOR UPDATE`，禁止跳过锁行 | connection/cursor/batch/scope/lock | rows |
| `apply_plan` | `backend/scripts/backfill_generation_turns.py` | 仅在现值为空或等于计划值时填充 Turn/reply，拒绝并发覆盖 | connection, RepairPlan | bool |
| `audit_invariants` | `backend/scripts/backfill_generation_turns.py` | 统计已关联 assistant 的缺失输入、角色、scope 和 Turn 不一致 | connection | dict |
| `run` | `backend/scripts/backfill_generation_turns.py` | 执行默认 dry-run 或分批 apply，管理无正文审计、事务与 checkpoint | connection/mode/batch/limit/scope/paths | stats, audits |
| `close_generation_turn` | `backend/migrations/120_turn_revision_foundation.sql` | 原子关闭 Turn、标记输入输出 revision 并推进会话版本 | conversation_id, task_id, output_message_id | JSONB |
| `enqueue_generation_turn` | `backend/migrations/121_conversation_actor_queue.sql` | 原子创建 pending Chat task 并校验、绑定输入/输出消息的 Turn 关系 | task_data, input_message_id, turn_id, execution_mode, delivery_context | JSONB |
| `claim_next_serial_generation_turn` | `backend/migrations/121_conversation_actor_queue.sql` | 锁定 conversation，按稳定队列顺序认领最早 serial task 并绑定 ContextSnapshot 基线 | conversation_id, lease_seconds, max_attempts | JSONB |
| `claim_branch_generation_turn` | `backend/migrations/121_conversation_actor_queue.sql` | 按 task_id 认领内部 branch task，不占用 serial owner | task_id, lease_seconds, max_attempts | JSONB |
| `renew_generation_lease` | `backend/migrations/121_conversation_actor_queue.sql` | 仅允许当前 fencing token 延长 running task 租约 | task_id, execution_token, lease_seconds | JSONB |
| `commit_generation_turn` | `backend/migrations/122_conversation_actor_terminal.sql`、`135_conversation_data_evidence.sql` | 7 参数函数保留原 Actor 终态；8 参数重载在同一事务复用原终态并按 closed revision 幂等提交数据证据 | task_id, execution_token, output_message_id, result_content, usage, credits_cost, tool_digest, data_evidence? | JSONB |
| `fail_generation_turn` | `backend/migrations/122_conversation_actor_terminal.sql` | 仅允许当前 fencing token 原子失败 running Chat task 并释放 owner | task_id, execution_token, error_code, error_message | JSONB |
| `cancel_generation_turn` | `backend/migrations/122_conversation_actor_terminal.sql` | 用户与租户范围校验后立即取消 pending/running Chat task 并使旧 token 失效 | task_id, user_id, org_id | JSONB |
| `create_actor_terminal_delivery` | `backend/migrations/124_conversation_delivery_outbox.sql` | Actor 企微 task 进入完成/失败终态时，在同一事务幂等创建投递 Outbox | trigger | trigger |
| `create_web_user_wecom_delivery` | `backend/migrations/134_web_user_wecom_delivery.sql` | Web task 入队时从同会话最近一次已校验企微 task 复制真实目标，移除旧 stream 状态并原子创建用户消息镜像 Outbox | trigger | trigger |
| `claim_conversation_delivery` | `backend/migrations/124_conversation_delivery_outbox.sql` | 使用 SKIP LOCKED、租约和稳定顺序认领一条待投递记录 | lease_seconds, max_attempts | JSONB |
| `renew_conversation_delivery` | `backend/migrations/124_conversation_delivery_outbox.sql` | 当前 fencing token 续租并保存分项投递检查点 | delivery_id, lease_token, lease_seconds, delivered_items | JSONB |
| `complete_conversation_delivery` | `backend/migrations/124_conversation_delivery_outbox.sql` | 当前有效投递权将 Outbox 原子标记为 delivered | delivery_id, lease_token, delivered_items | JSONB |
| `fail_conversation_delivery` | `backend/migrations/124_conversation_delivery_outbox.sql` | 当前投递权按有界指数退避重新排队，超限进入 dead | delivery_id, lease_token, error, delivered_items, max_attempts | JSONB |
| `enqueue_wecom_generation_turn` | `backend/migrations/125_wecom_actor_enqueue.sql` | 按稳定 ID 原子创建企微输入/助手消息并幂等进入 Actor serial queue | task_data, input/output ids, turn_id, input_content, delivery_context | JSONB |
| `update_wecom_conversation_setting` | `backend/migrations/126_wecom_conversation_settings.sql` | 行锁内按 user/org/source 校验并原子更新模型或思考模式 | conversation_id, user_id, setting_key/value, org_id | JSONB |
| Actor tenant RPC facades | `backend/migrations/127_actor_tenant_rpc_contract.sql` | 接收 OrgScopedDB 注入的 p_org_id，强校验租户后委托既有原子核心 | 原核心参数 + org_id | JSONB |
| `get_wecom_conversation_setting` / `set_wecom_conversation_setting` | `backend/services/wecom/conversation_settings.py` | 读取企微对话设置并通过原子 RPC 持久化，数据库为唯一事实源 | db, conversation/user/key/value/org | str \| dict |
| `enqueue_wecom_message` | `backend/services/wecom/actor_enqueue.py` | 从企微 msgid 派生稳定 task/message/turn ID，将智能机器人 stream 上下文写入 delivery_context，并以结构化内容调用原子入队 RPC | handler, msg, user_id, conversation_id, image_urls, file_payload, stream_context | WecomActorEnqueueResult |
| `stable_wecom_task_id` | `backend/services/wecom/actor_enqueue.py` | 返回与企微原子入队完全一致的稳定 task ID，供入站在唤醒 Worker 前注册 stream 保活 | msg, user_id | str |
| `identify_file` | `backend/services/assets/file_identity.py` | 依据解密后内容识别 CSV/TSV、Office、PDF 和媒体类型，生成安全规范名、MIME 与 SHA-256 | data, stable_id, provider_name?, content_disposition? | AssetIdentity |
| `resolve_asset_identity` | `backend/services/assets/asset_identity.py` | 将可信 Workspace/OSS URL 与 owner key 解析为不受 CDN 域名和 query 影响的 canonical provider/key | original_url, workspace_path?, org/scope/owner, allowed_hosts? | CanonicalAssetIdentity |
| `configured_asset_hosts` | `backend/services/assets/asset_identity.py` | 从当前 OSS/CDN 配置生成精确允许主机集合 | — | frozenset[str] |
| `is_allowed_asset_url` | `backend/services/assets/asset_identity.py` | 校验 HTTPS、精确主机、端口、认证信息和安全对象路径 | url, allowed_hosts? | bool |
| `_resolve_user_asset` | `backend/migrations/145_user_assets.sql` | 按 org/scope/owner key/provider/storage key 原子解析或创建 canonical 资产，并只补充空字段 | canonical asset fields | JSONB |
| `_bind_user_asset_ref` | `backend/migrations/145_user_assets.sql` | 按稳定 ref key 原子绑定业务来源，拒绝跨资产、actor、org 或来源事实冲突 | asset id + source fields | JSONB |
| `register_user_asset` | `backend/migrations/145_user_assets.sql` | 校验输入并在单个数据库事务中组合 canonical 资产解析与来源绑定 | asset draft + ref draft | JSONB |
| `list_admin_user_assets` | `backend/migrations/146_admin_user_assets_query.sql` | 通过用户来源关联过滤 ready canonical 资产，选择确定性代表 ref，并按 `(created_at,id)` 游标返回去重页和总数 | actor user/source/media/limit/cursor | JSONB |
| `AssetRegistryService.register_ready_asset` | `backend/services/assets/asset_registry.py` | 解析 canonical storage identity，并调用数据库 RPC 原子创建/复用资产本体与来源 ref | ReadyAssetDraft, AssetRefDraft | dict |
| `ReadyAssetDraft` | `backend/services/assets/asset_registry.py` | 已持久化资产本体的类型化登记协议，不携带业务来源字段 | storage scope/owner, media, URL/path/hash/metadata | dataclass |
| `AssetRefDraft` | `backend/services/assets/asset_registry.py` | 上传、任务、消息、生成记录或企微附件的稳定来源关联协议 | ref key, actor, source/ref kind, source IDs/metadata | dataclass |
| `register_web_upload_best_effort` | `backend/services/assets/asset_registry.py` | Web 文件持久化成功后登记未关联消息的 upload 资产；索引异常记录上下文但不改判上传失败 | db, user/org, URL/name/mime/size/path/thumbnail | dict \| None |
| `register_task_media_best_effort` | `backend/services/assets/asset_registry.py` | 普通图片/视频任务完成持久化后逐条登记 generated 资产；单条索引异常不改判任务失败 | db, task, content_parts | list[dict] |
| `register_wecom_attachment_best_effort` | `backend/services/assets/asset_registry.py` | 企微附件 RPC 暂存成功后，按 attachment ID 幂等登记用户或频道 upload 资产；索引异常不回滚附件 | db, attachment/message/conversation, actor/org/storage, file_payload | dict \| None |
| `register_message_media_best_effort` | `backend/services/assets/asset_registry.py` | 按 assistant 消息最终 content 序号登记 MediaTool/ImageAgent generated 资产；索引异常不影响 Actor 结果 | db, actor/org/storage, conversation/message, indexed_parts | list[dict] |
| `project_row` / `media_parts` | `backend/scripts/backfill_user_assets.py` | 将五类历史事实和旧 ContentPart/result 形态确定性投影为现有资产本体与来源 ref 协议 | source, row/content | list[(ReadyAssetDraft, AssetRefDraft)] |
| `run` | `backend/scripts/backfill_user_assets.py` | 按来源独立复合 checkpoint 批量 dry-run/apply；复用原子登记 RPC，失败批次不推进游标并输出孤儿对账 | conn, apply, batch_size, checkpoint, limit? | BackfillStats |
| `PsycopgRpcClient` | `backend/scripts/backfill_user_assets.py` | 在维护窗口的 psycopg 事务中为既有 AssetRegistryService 提供 register_user_asset RPC 适配 | conn, rpc params | RPC payload |
| `prepare_archive_rows` | `backend/services/kuaimai/erp_sync_executor.py` | 在 ERP 热表写入冷表前将查询返回的 `exception_tags` list 恢复为 PostgreSQL `TEXT[]` 字面量 | rows | normalized rows |
| `MediaToolMixin._generate_video` | `backend/services/media_tool_executor.py` | Chat 视频工具成功后先持久化到 Workspace/OSS，再确认扣费并输出标准 video emit payload；临时 URL 降级不登记 ready 资产 | prompt/tool context | AgentResult |
| `_update_message_image_part` | `backend/api/routes/image_ecom.py` | 在指定 conversation/message 内把失败图片按图片序号原位替换为公开 ImagePart，拒绝越界并返回真实 content 数组下标 | db, message/conversation ID, image ordinal, emit payload | int |
| `list_user_assets` | `backend/api/routes/admin_user_assets.py` | 超管校验目标用户后调用资产查询 RPC，按来源/媒体类型和不透明复合游标读取 ready canonical 资产 | uid, source_type, media_type?, limit, cursor? | items/next_cursor/has_more/total |
| `_encode_cursor` / `_decode_cursor` | `backend/api/routes/admin_user_assets.py` | 编解码并严格校验用户资产复合游标中的 ISO 时间和 UUID | created_at/id 或 opaque cursor | str / tuple |
| `download_user_assets_zip` | `backend/api/routes/admin_users_zip.py` | 超管提交 asset_ids 后先由 user_asset_refs 完整复验目标用户归属，再校验 canonical 资产 ready 状态和安全下载主机并流式打包 | uid, asset_ids | StreamingResponse |
| `WecomMediaDownloader.download_and_decrypt` | `backend/services/wecom/media_downloader.py` | 限流下载并可选解密企微媒体，保留 Content-Type 与 Content-Disposition 响应元数据 | url, aeskey? | DownloadedMedia \| None |
| `normalize_wecom_message` / `parse_message_content` | `backend/services/wecom/message_normalizer.py` | 规范化智能机器人回调；私聊缺少 chatid 时使用发送者，群聊强制 chatid，并统一解析媒体字段 | body, org_id, corp_id | WecomIncomingMessage |
| `resolve_channel_conversation` / `resolve_wecom_conversation` | `backend/services/wecom/channel_conversation.py`、`backend/migrations/128_wecom_channel_conversations.sql` | 按 org/corp/chatid 原子解析私聊或群共享 conversation；首次私聊可认领未绑定的历史企微对话 | user/corp/chat/type/org | conversation UUID |
| `stage_wecom_attachment` | `backend/services/wecom/attachment_service.py`、`backend/migrations/129_conversation_attachments.sql` | 以企微 msgid 幂等创建 completed 文件消息和 ready 活动附件，不创建生成任务 | conversation/sender/file/scope/org | StagedAttachment |
| `stage_wecom_attachment_v2` | `backend/migrations/131_attachment_asset_lifecycle.sql` | 在会话行锁内校验资产哈希；连续上传加入 collecting 集合，已绑定集合在新上传时转为 replaced | conversation/message/provider/asset/scope/org | JSONB |
| `bind_task_attachments` / `current_attachment_parts` | `backend/migrations/131_attachment_asset_lifecycle.sql` | 冻结当前附件集合到 task/input/turn，不消费 active 集合；任务失败和重放继续使用原集合 | task/turn/input/conversation/org | int / JSONB |
| `enqueue_wecom_task_record` | `backend/migrations/132_wecom_channel_task_enqueue.sql` | 在上层完成渠道绑定校验后，幂等写入 user 或 channel 企微会话的 Actor task | task_data, input, turn, mode, delivery | JSONB |
| `build_resource_manifest` | `backend/services/handlers/resource_manifest.py` | 优先按 task_attachment_refs 构造不可变资源清单；Web/旧任务只回退到固定输入消息内的媒体块 | db, task/input/conversation/turn/org/content | ResourceManifest |
| `FileToolMixin._search_manifest` | `backend/services/agent/file_tool_mixin.py` | file_search 默认只查询当前任务资源，返回资产 ID 和稳定路径，不扫描 Workspace | executor, args | AgentResult |
| `_validate_resource_scope` | `backend/services/agent/file_analysis_service.py` | file_analyze 默认拒绝不属于当前 ResourceManifest 的路径；workspace scope 必须显式声明 | owner, executor, args, path | AgentResult \| None |
| `build_plan` / `apply_plan` | `backend/scripts/reconcile_wecom_attachments.py` | 从租户隔离的 Workspace 原文件重建资产身份；显式 apply 时原子更新附件事实与源消息 FilePart | row/workspace, cursor/plan | ReconcilePlan / None |
| `resolve_asset_path` | `backend/scripts/reconcile_wecom_attachments.py` | 按 user/channel scope 重算资产根目录并拒绝路径穿越，不信任历史绝对路径 | attachment row, workspace_root | Path |
| `WecomFileMixin._prepare_wecom_file` | `backend/services/wecom/wecom_file_mixin.py` | 解密后按真实内容识别类型和规范名，再按 msgid 稳定保存；私聊进入个人 Workspace，群聊进入 channel Workspace | msg, reply_ctx, user_id, org_id | dict \| None |
| `WecomReplyMixin` | `backend/services/wecom/wecom_reply_mixin.py` | 旧同步链路的结果格式化、企微双通道回复和 Web 会话更新通知 | - | mixin |
| `WecomIngressMixin._process_incoming_content` | `backend/services/wecom/wecom_ingress_mixin.py` | FILE 固定进入 Actor；其他支持类型按企微 Actor 开关分流原子入队或旧同步生命周期 | msg, reply_ctx, user_id, conversation_id, image_urls | None |
| `GenerationClaim.from_rpc` | `backend/services/conversation_execution.py` | 校验 claimed RPC 结果并构造不可变执行权对象；非 claimed 结果返回 None | data, conversation_id, execution_mode | GenerationClaim \| None |
| `ConversationExecutionService.claim_serial` | `backend/services/conversation_execution.py` | 通过数据库 RPC 认领 conversation 最早 serial task | conversation_id | GenerationClaim \| None |
| `ConversationExecutionService.claim_branch` | `backend/services/conversation_execution.py` | 通过数据库 RPC 精确认领内部 branch task | task_id, conversation_id | GenerationClaim \| None |
| `ConversationExecutionService.execute_claim` | `backend/services/conversation_execution.py` | 协调纯执行器、租约续期、丢权取消以及原子 commit/fail；确定性数据库完整性错误立即落正式失败终态，连接结果未知时保留重试语义 | claim | Dict |
| `RedisConversationWakeup.publish` | `backend/services/conversation_worker.py` | Best-effort 发布 conversation 唤醒；Redis 故障不影响数据库兜底扫描 | conversation_id, org_id | bool |
| `RedisConversationWakeup.listen` | `backend/services/conversation_worker.py` | 订阅 Actor 唤醒频道并在断连后退避重连 | handler | None |
| `ConversationWorker.start` | `backend/services/conversation_worker.py` | 启动有界数据库扫描循环和可选 Redis 唤醒监听 | - | None |
| `ConversationWorker.stop` | `backend/services/conversation_worker.py` | 停止扫描、关闭监听，并在超时后取消本地执行 | - | None |
| `ConversationWorker.wake` | `backend/services/conversation_worker.py` | 合并同 conversation 的本地或 Redis 唤醒 | conversation_id | None |
| `ConversationWorker.scan_once` | `backend/services/conversation_worker.py` | 扫描 pending/running Chat task，按 serial conversation 或 branch task 去重并受并发上限调度 | - | int |
| `append_final_turn_blocks` | `backend/services/handlers/chat/outcome_builder.py` | 按流式时序将最后一轮尚未收割的 thinking/text 追加到内容块 | blocks, thinking, thinking_committed, thinking_duration_ms, text | None |
| `build_content_parts` | `backend/services/handlers/chat/outcome_builder.py` | 将可信内部内容块转换为 ContentPart；空块回退为普通文本且不扫描媒体标记 | blocks, fallback_text, fallback_thinking, fallback_thinking_duration_ms | List[ContentPart] |
| `prepare_chat_stream` | `backend/services/handlers/chat/stream_setup.py` | 基于固定上下文准备 Provider、权限工具、trace、staging、执行预算与当前模型 ContextBudget，不读写任务终态 | handler, content, user_id, conversation_id, task_id, model_id, permission_mode, needs_google_search, params, context_anchor | PreparedChatStream |
| `build_context_receipt` | `backend/services/agent/runtime/context/receipt.py` | 只读生成不含正文的影子上下文回执，记录消息块、工具 Schema、Token 估算与稳定 hash | messages, tools, conversation_id, task_id, model_id | ContextReceipt |
| `derive_context_budget` | `backend/services/agent/runtime/context/budget.py` | 按模型窗口与最大输出推导 output reserve、safety margin、usable input 及 75%/85%/92% 压缩阈值 | context_window, max_output_tokens | ContextBudget |
| `resolve_context_budget` | `backend/services/agent/runtime/context/budget.py` | 从统一模型注册表解析 ContextBudget，未知模型使用 128K/8K 保守默认能力 | model_id | ContextBudget |
| `call_summary_model` | `backend/services/agent/runtime/context/summary_model.py` | Context Runtime 私有摘要模型边界，限制输出长度并在超时或响应异常时失败关闭 | model, source, system_prompt?, max_chars? | Optional[str] |
| `record_context_event` | `backend/services/agent/runtime/context/telemetry.py` | 以 `gen_ai.context_*` 稳定名称记录不含正文的 Receipt、缓存、压缩和 Evidence 结构化观测事件 | event, fields | None |
| `build_evidence_model_view` | `backend/services/agent/runtime/context/providers/evidence.py` | 按 8KB/64KB 阈值把结构化 Evidence 投影为完整、采样、元数据或引用视图 | artifact_id, source, rows, columns, file_ref, query_scope, metric_definitions | EvidenceModelProjection |
| `build_evidence_tools` | `backend/config/evidence_tools.py` | 构建仅在固定 Snapshot 存在 Evidence 时开放的只读 Search/Get 工具 Schema | - | List[Dict] |
| `prepare_tool_turn` | `backend/services/handlers/chat/tool_loop.py` | 构建动态工具列表并追加上下文、退出附件和权限提醒 | core_tools, discovered_names, org_id, turn, messages, tool_context, permission | List[Dict] |
| `apply_tool_results` | `backend/services/handlers/chat/tool_loop.py` | 将工具结果回填模型消息和 tool_step，并返回待注入图片 URL | tool_results, messages, content_blocks, start_times, tool_context | List[str] |
| `compact_tool_context` | `backend/services/handlers/chat/tool_loop.py` | 按当前模型 ContextBudget 的 75%/85%/92% 阈值统一归档、摘要和兜底，并把 task scope 传给循环压缩协调器 | messages, context_budget, turn, compaction_scope? | None |
| `collect_tool_result` | `backend/services/agent/runtime/artifact_collector.py` | 将结构化 AgentResult 旁路映射为 Run 内产物证据，禁止从 Markdown 反向取数 | result, tool_call_id | Tuple[ArtifactEvidence, ...] |
| `ArtifactLedger.record` | `backend/services/agent/runtime/artifact_ledger.py` | 按稳定 fingerprint 幂等登记 Run 内产物证据 | evidence | bool |
| `DataAccuracyPolicy.validate_artifact` | `backend/services/agent/runtime/policies/data_accuracy.py` | 校验数据产物状态、行结构、列结构和受控文件引用 | contract, evidence, payload | PolicyResult |
| `normalize_tool_result` | `backend/services/agent/runtime/validation/normalizer.py` | 将 AgentResult、旧字符串结果与执行异常确定性归一为唯一工具终态，结构化信号优先 | result, tool_call_id, tool_name, audit_status?, effect?, effective_tool_name? | ValidatedToolResult |
| `resolve_tool_effect` | `backend/services/agent/runtime/validation/effects.py` | 将项目现有SafetyLevel投影为副作用类型；危险工具按非幂等写处理，缺失元数据安全默认只读 | tool_name | ToolEffect |
| `ValidationTracker.observe` | `backend/services/agent/runtime/validation/tracker.py` | 记录单 Run 连续失败、同错误指纹和有效进展 | result | int |
| `decide_recovery` | `backend/services/agent/runtime/validation/recovery.py` | 基于统一终态、失败轨迹、剩余轮次和固定策略生成纯恢复决策 | result, tracker, turns_remaining, policy? | RecoveryDecision |
| `build_recovery_observation` | `backend/services/agent/runtime/validation/observation.py` | 将校验失败投影为不包含系统事实的稳定模型 Observation | result | str |
| `ValidationRuntime.observe_result` | `backend/services/agent/runtime/validation/runtime.py` | 单 Run 统一归一、追踪、恢复决策和 Receipt 登记门面；主Chat已以fail-open观察模式消费，不执行工具或写数据库 | result, tool_call_id, tool_name, model_step, turns_remaining, audit_status?, effect?, duration_ms? | Tuple[ValidatedToolResult, RecoveryDecision] |
| `ToolLoopExecutionMixin._execute_tools` | `backend/services/agent/tool_loop_execution.py` | 保持ERP/定时任务原工具执行协议，并在真实结果后以fail-open方式旁路记录统一Validation Receipt | completed, selected_tools, turn_text, hook_ctx, turn_prompt_tokens?, turn_completion_tokens? | str |
| `ToolLoopExecutionMixin._observe_validation_result` | `backend/services/agent/tool_loop_execution.py` | 将工具终态结果、轮次、剩余预算、审计状态和耗时送入单Run ValidationRuntime；不控制旧StopPolicy | result, tool_call_id, tool_name, hook_ctx, audit_status, elapsed_ms | None |
| `ToolLoopExecutionMixin._compare_validation_decision` | `backend/services/agent/tool_loop_execution.py` | 将旧StopPolicy与新Recovery按继续/停止控制意图进行旁路比较，保留双方原始决策且fail-open | old_decision, model_step | None |
| `build_run_contract` | `backend/services/agent/runtime/runtime_contract.py` | 从调用方私有参数构建显式交付合同，不从用户文本或模型输出推断授权 | params | RunContract |
| `evaluate_completion` | `backend/services/agent/runtime/completion_gate.py` | 根据必需产物、ready 证据和预算状态确定继续、完成或降级 | contract, snapshot, budget_exhausted | CompletionResult |
| `RuntimeState.persistence_projection` | `backend/services/agent/runtime/runtime_state.py` | 将 ready DATA_RESULT 投影为 Actor 可原子提交的受限 JSON 证据 | - | List[Dict] |
| `load_data_context_snapshot` | `backend/services/handlers/data_context_snapshot.py` | 按 conversation 和 base revision 加载、去重跨 Turn 数据证据 | db, conversation_id, base_revision | DataContextSnapshot |

### 滚动管理模块 (Scroll Management)

> **重构记录（2026-02-03）**：从 Virtuoso 迁移到 Virtua，统一为 `useVirtuaScroll` 单一入口。Virtua 更轻量（~3KB）且更好支持动态高度。

#### 前端函数

| 函数名 | 文件路径 | 功能描述 | 参数 | 返回值 |
|--------|----------|----------|------|--------|
| `useVirtuaScroll` | `frontend/src/hooks/useVirtuaScroll.ts` | Virtua 滚动管理统一入口（智能自动滚动、用户状态检测） | UseVirtuaScrollOptions | UseVirtuaScrollReturn |

**UseVirtuaScrollOptions**：
- `conversationId`: 当前对话 ID
- `messages`: 消息列表
- `loading`: 是否正在加载
- `isStreaming`: 是否正在流式生成

**UseVirtuaScrollReturn**：
- `vlistRef`: VList 实例引用
- `userScrolledAway`: 用户是否主动滚走
- `hasNewMessages`: 是否有新消息
- `showScrollButton`: 是否显示滚动按钮
- `handleScroll`: 滚动事件回调（传给 VList onScroll）
- `scrollToBottom`: 滚动到底部方法
- `setUserScrolledAway`: 设置用户滚走状态
- `setHasNewMessages`: 设置新消息状态


### 重新生成模块 (Regenerate)

#### 前端函数

| 函数名 | 文件路径 | 功能描述 | 参数 | 返回值 |
|--------|----------|----------|------|--------|
| `useRegenerateHandlers` | `frontend/src/hooks/useRegenerateHandlers.ts` | 重新生成处理器组合 Hook | RegenerateHandlersOptions | {handleRegenerate, handleRegenerateSingle} |
| `handleRegenerateSingle` | `frontend/src/hooks/useRegenerateHandlers.ts` | 单图重新生成（多图模式下重新生成指定 index 的图片） | targetMessage, imageIndex, userMessage | Promise<void> |
| `useRegenerateFailedMessage` | `frontend/src/hooks/regenerate/useRegenerateFailedMessage.ts` | 失败消息原地重新生成 | UseRegenerateFailedMessageOptions | (messageId, targetMessage) => Promise<void> |
| `useRegenerateAsNewMessage` | `frontend/src/hooks/regenerate/useRegenerateAsNewMessage.ts` | 成功消息新增对话重新生成 | UseRegenerateAsNewMessageOptions | (userMessage) => Promise<void> |

### 任务恢复模块 (Task Restoration)

> **重构说明**：轮询逻辑已被 WebSocket 实时推送替代。任务恢复通过 `taskRestoration.ts` 两阶段架构恢复。
> **2026-03-01 修复**：WS 订阅从 `external_task_id` 改为优先使用 `client_task_id`，与后端推送 ID 一致。

#### 前端函数

| 函数名 | 文件路径 | 功能描述 | 参数 | 返回值 |
|--------|----------|----------|------|--------|
| `fetchPendingTasks` | `frontend/src/utils/taskRestoration.ts` | 获取进行中的任务（含 client_task_id） | - | Promise<PendingTask[] \| null> |
| `restoreTaskPlaceholders` | `frontend/src/utils/taskRestoration.ts` | Phase 1: 获取 pending 任务并创建占位符（纯 HTTP） | - | Promise<RestorationResult \| null> |
| `subscribeRestoredTasks` | `frontend/src/utils/taskRestoration.ts` | Phase 2: 为恢复的任务订阅 WS（优先 client_task_id） | result, subscribeToTask | void |
| `restoreMediaTask` | `frontend/src/utils/taskRestoration.ts` | 恢复媒体任务占位符 | task: PendingTask | void |

### 统一消息发送模块 (Message Sender)

#### 前端函数

| 函数名 | 文件路径 | 功能描述 | 参数 | 返回值 |
|--------|----------|----------|------|--------|
| `sendUnifiedMessage` | `frontend/src/services/messageSender/unifiedSender.ts` | 统一消息发送入口（chat/image/video） | UnifiedMessageParams | Promise<void> |
| `createMessageLifecycle` | `frontend/src/services/messageSender/lifecycle.ts` | 创建消息生命周期标识 | - | MessageLifecycle |
| `callBackendAPI` | `frontend/src/services/messageSender/backendAPI.ts` | 后端 API 调用（路由到 chat/image/video API） | UnifiedMessageParams, MessageLifecycle | Promise<UnifiedAPIResponse> |
| `determineMessageType` | `frontend/src/services/messageSender/unifiedSender.ts` | 判断消息类型 | Message | MessageType |

### 重新生成模块 (Regeneration)

#### 前端函数

| 函数名 | 文件路径 | 功能描述 | 参数 | 返回值 |
|--------|----------|----------|------|--------|
| `regenerateMessage` | `frontend/src/utils/regenerate/index.ts` | 统一重新生成入口（使用 sendUnifiedMessage） | targetMessage, userMessage, RegenerateContext | Promise<void> |

### 性能监控模块 (Performance Monitoring)

#### 前端函数

| 函数名 | 文件路径 | 功能描述 | 参数 | 返回值 |
|--------|----------|----------|------|--------|
| `PerformanceMonitor` | `frontend/src/utils/performanceMonitor.ts` | 性能监控管理器类 | - | PerformanceMonitor |
| `PerformanceMonitor.start` | `frontend/src/utils/performanceMonitor.ts` | 开始性能测量 | name, metadata? | void |
| `PerformanceMonitor.end` | `frontend/src/utils/performanceMonitor.ts` | 结束性能测量并记录 | name, additionalMetadata? | number \| null |
| `PerformanceMonitor.measure` | `frontend/src/utils/performanceMonitor.ts` | 测量异步操作性能 | name, fn, metadata? | Promise<T> |
| `PerformanceMonitor.measureSync` | `frontend/src/utils/performanceMonitor.ts` | 测量同步操作性能 | name, fn, metadata? | T |
| `PerformanceMonitor.getPageMetrics` | `frontend/src/utils/performanceMonitor.ts` | 获取页面性能指标 | - | Record<string, number> \| null |
| `PerformanceMonitor.logPageMetrics` | `frontend/src/utils/performanceMonitor.ts` | 记录页面性能指标 | - | void |
| `measureAsync` | `frontend/src/utils/performanceMonitor.ts` | 便捷函数：测量异步操作 | name, fn, metadata? | Promise<T> |
| `measureSync` | `frontend/src/utils/performanceMonitor.ts` | 便捷函数：测量同步操作 | name, fn, metadata? | T |

### 测试工具模块 (Testing Utils)

#### 后端函数

| 函数名 | 文件路径 | 功能描述 | 参数 | 返回值 |
|--------|----------|----------|------|--------|
| `pytest_configure` | `backend/testing/pytest_policy.py` | 在应用模块导入前固定测试环境并关闭空闲数据库连接 | - | None |
| `classify_nodeid` | `backend/testing/pytest_policy.py` | 为无法直接修改的旧测试节点返回执行层级 | nodeid | str \| None |
| `pytest_collection_modifyitems` | `backend/testing/pytest_policy.py` | 收集阶段为旧测试应用集中式层级 marker | items | None |

#### 前端函数

| 函数名 | 文件路径 | 功能描述 | 参数 | 返回值 |
|--------|----------|----------|------|--------|
| `customRender` | `frontend/src/test/testUtils.tsx` | 自定义 render 函数 | ui, options? | RenderResult |
| `customRenderHook` | `frontend/src/test/testUtils.tsx` | 自定义 renderHook 函数 | render, options? | RenderHookResult |
| `mockAsyncFn` | `frontend/src/test/testUtils.tsx` | 创建 Mock 异步函数 | value, delayMs? | MockInstance |
| `delay` | `frontend/src/test/testUtils.tsx` | 延迟工具函数 | ms | Promise<void> |

### 预定义常量

#### 性能标记常量 (Performance Markers)

| 常量名 | 值 | 功能描述 |
|--------|-----|----------|
| `PerfMarkers.MESSAGE_SEND` | 'message:send' | 消息发送性能标记 |
| `PerfMarkers.MESSAGE_STREAM` | 'message:stream' | 流式响应性能标记 |
| `PerfMarkers.MESSAGE_LOAD` | 'message:load' | 消息加载性能标记 |
| `PerfMarkers.IMAGE_GENERATION` | 'image:generation' | 图片生成性能标记 |
| `PerfMarkers.IMAGE_UPLOAD` | 'image:upload' | 图片上传性能标记 |
| `PerfMarkers.IMAGE_POLLING` | 'image:polling' | 图片轮询性能标记 |
| `PerfMarkers.VIDEO_GENERATION` | 'video:generation' | 视频生成性能标记 |
| `PerfMarkers.VIDEO_POLLING` | 'video:polling' | 视频轮询性能标记 |
| `PerfMarkers.CONVERSATION_SWITCH` | 'ui:conversation-switch' | 对话切换性能标记 |
| `PerfMarkers.SCROLL_POSITION` | 'ui:scroll-position' | 滚动位置性能标记 |
| `PerfMarkers.RENDER` | 'ui:render' | 渲染性能标记 |
| `PerfMarkers.API_REQUEST` | 'api:request' | API 请求性能标记 |
| `PerfMarkers.API_RESPONSE` | 'api:response' | API 响应性能标记 |

#### 媒体默认值常量 (Media Defaults)

| 常量名 | 值 | 功能描述 |
|--------|-----|----------|
| `MEDIA_DEFAULTS.IMAGE_MODEL` | 'google/nano-banana' | 默认图片模型 |
| `MEDIA_DEFAULTS.VIDEO_MODEL` | 'sora-2-text-to-video' | 默认视频模型 |
| `MEDIA_DEFAULTS.I2V_MODEL` | 'sora-2-image-to-video' | 默认图生视频模型 |

### 聊天模块 (Chat Module) - 简要列表

> 详细组件列表见下方"聊天组件模块 (Chat Components)"

| 组件名 | 文件路径 | 功能描述 |
|--------|----------|----------|
| `Chat` | `frontend/src/pages/Chat.tsx` | 聊天主页面，管理侧边栏、消息区域、输入区域 |
| `Sidebar` | `frontend/src/components/chat/Sidebar.tsx` | 左侧栏，包含新建对话、对话列表、用户菜单 |
| `ConversationList` | `frontend/src/components/chat/ConversationList.tsx` | 对话列表主组件（302行，已拆分） |
| `MessageArea` | `frontend/src/components/chat/MessageArea.tsx` | 消息区域，显示对话消息 |
| `InputArea` | `frontend/src/components/chat/InputArea.tsx` | 输入区域，模型选择、图片上传、高级设置、流式发送 |

### 主图详情制作页面（UI 第一阶段）

| 函数/组件 | 文件路径 | 功能描述 | 参数 | 返回值 |
|---|---|---|---|---|
| `DetailPage` | `frontend/src/pages/DetailPage.tsx` | 独立五步制作页，连接 AI 帮写请求、弹窗和需求回填 | - | JSX |
| `DetailPageHeader` | `frontend/src/components/detail-page/DetailPageHeader.tsx` | 顶部返回、积分和用户入口 | - | JSX |
| `StepBar` | `frontend/src/components/detail-page/StepBar.tsx` | 五步进度展示 | step | JSX |
| `ProductImageSection` | `frontend/src/components/detail-page/ProductImageSection.tsx` | 产品图/参考图本地选择与共享上限展示 | images, actions | JSX |
| `GenerationSettings` | `frontend/src/components/detail-page/GenerationSettings.tsx` | Step 1生成参数表单及 AI 帮写入口 | form, requirementAssistDisabled, actions | JSX |
| `AnalyzingPanel` | `frontend/src/components/detail-page/AnalyzingPanel.tsx` | Step 2 分阶段分析反馈与取消 | stage, onCancel | JSX |
| `PlanReviewPanel` | `frontend/src/components/detail-page/PlanReviewPanel.tsx` | Step 3 规划确认与页面操作 | plan, actions | JSX |
| `PlanCard` | `frontend/src/components/detail-page/PlanCard.tsx` | 单张规划编辑、折叠提示词与删除 | item, actions | JSX |
| `GenerationProgress` | `frontend/src/components/detail-page/GenerationProgress.tsx` | Step 4 整组生成进度和条目列表 | items, onRetry | JSX |
| `GenerationCard` | `frontend/src/components/detail-page/GenerationCard.tsx` | 单张等待、生成、完成和失败状态 | item, onRetry | JSX |
| `ResultGallery` | `frontend/src/components/detail-page/ResultGallery.tsx` | Step 5 结果统计与操作 | items, actions | JSX |
| `useDetailPageStore` | `frontend/src/stores/useDetailPageStore.ts` | 页面专用 Zustand 状态 | - | DetailPageState |
| `attach_detail_project_image` | `backend/migrations/118_detail_projects.sql` | 原子创建/获取详情草稿并关联工作区图片 | user/org/path/category | project/image IDs |
| `DetailProjectService.get_current` | `backend/services/detail_project_service.py` | 恢复当前用户与企业空间的详情草稿及图片状态 | DB/user/org | project/null |
| `DetailProjectService.attach_image` | `backend/services/detail_project_service.py` | 校验工作区图片并调用原子关联函数 | workspace_path/category | latest project |
| `DetailProjectService.update_settings` | `backend/services/detail_project_service.py` | 使用版本锁保存草稿白名单设置 | project/version/settings | latest project |
| `DetailProjectService.remove_image` / `update_category` / `reorder_images` | `backend/services/detail_project_service.py` | 事务内编辑图片引用并递增草稿版本 | project/image/version | latest project |
| `get_current_detail_project` / `attach_detail_project_image` | `backend/api/routes/detail_project.py` | 主图详情页草稿读取和图片关联 API | OrgCtx/request | unified envelope |
| `getCurrentDetailProject` / `attachDetailImage` / `saveDetailSettings` / `removeDetailImage` | `frontend/src/services/detailProject.ts` | 主图详情页真实草稿 API 客户端 | project/image/form | project/null |
| `useDetailPageStore.hydrateDraft` / `addImages` / `removeImage` | `frontend/src/stores/useDetailPageStore.ts` | 草稿恢复、串行真实上传关联与引用删除 | files/project | store state |
| `WorkspaceImagePicker` | `frontend/src/components/detail-page/WorkspaceImagePicker.tsx` | 复用工作区列表、搜索和预览选择已有图片 | remaining/selection | workspace paths |
| `Select` | `frontend/src/components/ui/Select.tsx` | 基于 Radix Dropdown 的锚定式表单选择器 | value/options | selected value |
| `addImages` | `frontend/src/stores/useDetailPageStore.ts` | 校验并添加本地预览图片 | category, files | void |
| `removeImage` | `frontend/src/stores/useDetailPageStore.ts` | 删除图片并释放 ObjectURL | id | void |
| `setStep` | `frontend/src/stores/useDetailPageStore.ts` | 切换当前步骤 | step | void |
| `updateForm` | `frontend/src/stores/useDetailPageStore.ts` | 更新表单并同步类型默认比例 | patch | void |
| `startAnalysis` / `cancelAnalysis` | `frontend/src/stores/useDetailPageStore.ts` | 启动或取消 Mock 分阶段分析 | - | void |
| `updatePlanItem` / `removePlanItem` | `frontend/src/stores/useDetailPageStore.ts` | 编辑或删除规划并保证至少一张 | id, patch | void |
| `replan` | `frontend/src/stores/useDetailPageStore.ts` | 按当前数量重建 Mock 规划 | - | void |
| `startGeneration` | `frontend/src/stores/useDetailPageStore.ts` | 启动逐张 Mock 生成并处理失败退款 | - | void |
| `retryGeneration` | `frontend/src/stores/useDetailPageStore.ts` | 重试单张并追加结果版本 | id | void |
| `backToPlan` / `restart` | `frontend/src/stores/useDetailPageStore.ts` | 返回规划或保留输入再次制作 | - | void |
| `setMockScenario` | `frontend/src/stores/useDetailPageStore.ts` | 选择 Mock 演示场景 | scenario | void |
| `reset` | `frontend/src/stores/useDetailPageStore.ts` | 恢复页面默认状态 | - | void |

### 消息服务模块 (Message Service)

#### 后端函数

| 函数名 | 文件路径 | 功能描述 | 参数 | 返回值 |
|--------|----------|----------|------|--------|
| `generate_message` | `backend/api/routes/message.py` | 统一消息生成入口（send/retry/regenerate） | body: GenerateRequest | GenerateResponse |
| `_do_generate_chat_message` | `backend/api/routes/message.py` | 校验 Web Chat 对话、注入上下文并调用统一原子准备编排 | conversation/body/context/db/user/request/handler | GenerateResponse |
| `_record_generation_feedback` | `backend/api/routes/message.py` | 为 retry/regenerate 操作异步记录用户隐式反馈 | db, user_id, body, generation_type, conversation_id | None |
| `MessageIdempotencyService.claim` | `backend/services/message_idempotency_service.py` | 原子抢占消息生成请求，处理指纹冲突、处理中状态与终态重放 | request, conversation_id, body | IdempotencyClaim \| None |
| `MessageIdempotencyService.ensure_identity` | `backend/services/message_idempotency_service.py` | 为旧客户端按租户、对话和幂等键确定性补齐 request/task/assistant ID | request, conversation_id, body | None |
| `MessageIdempotencyService.build_fingerprint` | `backend/services/message_idempotency_service.py` | 对稳定业务请求字段生成 SHA-256 指纹 | conversation_id, body | str |
| `MessageIdempotencyService.complete` / `fail` | `backend/services/message_idempotency_service.py` | 持久化可重放的成功或业务失败终态 | claim, response/error | None |
| `MessageIdempotencyService.fail_unexpected` | `backend/services/message_idempotency_service.py` | 最佳努力持久化脱敏的未知异常终态且不覆盖原异常 | claim, error | None |
| `prepare_and_start_chat_generation` | `backend/api/routes/message_chat_preparation.py` | 原子准备 Web Chat 的消息、Turn、本地 task 和 ContextAnchor 后入队 Actor | db, handler, conversation/user/org/request, body | GenerateResponse |
| `prepare_and_start_image_generation` | `backend/api/routes/message_image_preparation.py` | 原子准备 Web 普通图片的消息、Turn 和 1–4 个 preparing task，再启动供应商提交 | db, handler, conversation service, scope/request/body | GenerateResponse |
| `submit_prepared_image_task` | `backend/services/handlers/image_prepared_submission.py` | 使用稳定本地 task 锁积分、提交图片供应商，并 attach 成功结果或记录失败终态 | handler, local task, adapter, batch/model/credit/request context | str \| None |
| `resolve_prepared_batch` | `backend/services/handlers/image_request_settings.py` | fail-closed 验证已准备 task 数量和 batch ID，禁止退回到供应商先调用路径 | metadata, num_images | tuple |
| `prepare_and_start_video_generation` | `backend/api/routes/message_video_preparation.py` | 原子准备 Web 视频消息、Turn 和 preparing task，再启动供应商提交 | db, handler, conversation service, scope/request/body | GenerateResponse |
| `resolve_video_submission_settings` | `backend/services/handlers/video_prepared_submission.py` | 统一解析视频实际模型、输入模式、时长与积分 | handler, content, params | VideoSubmissionSettings |
| `submit_prepared_video_task` | `backend/services/handlers/video_prepared_submission.py` | 使用稳定本地 task 锁积分、提交视频供应商，并 attach 成功结果或记录失败终态 | handler, local task, user/request/settings/client context | str |
| `message_idempotency_cleanup_loop` | `backend/core/message_idempotency_cleanup.py` | 每小时调用数据库函数清理超过 24 小时的幂等记录 | db | None |
| `MessageResponse.parse_generation_params` | `backend/schemas/message.py` | Supabase JSONB 字符串自动转 dict（field_validator） | v: Any | Any |
| `get_messages` | `backend/services/message_service.py` | 获取对话消息列表 | conversation_id, user_id, limit, offset, before_id | dict |
| `delete_message` | `backend/services/message_service.py` | 删除单条消息（权限验证后物理删除） | message_id, user_id | dict |
| `create_message` | `backend/services/message_service.py` | 创建消息记录 | conversation_id, user_id, content, role, credits_cost | dict |
| `ChatHandler.start` | `backend/services/handlers/chat_handler.py` | 启动聊天任务（smart mode 时 deferred routing） | message_id, conversation_id, user_id, content, params | task_id |
| `ChatRoutingMixin._route_and_stream` | `backend/services/handlers/chat_routing_mixin.py` | Smart mode 异步路由：Agent Loop + 记忆并行，路由完成后分发 | task_id, message_id, conversation_id, user_id, content, _params, metadata | None |
| `ChatRoutingMixin._reroute_to_media` | `backend/services/handlers/chat_routing_mixin.py` | 重路由到 Image/Video Handler（非 chat 路由结果） | task_id, message_id, ..., gen_type, model_id | None |
| `ImageHandler.start` | `backend/services/handlers/image_handler.py` | 启动图片生成任务（异步） | message_id, conversation_id, user_id, content, params | task_id |
| `ImageHandler.preflight` | `backend/services/handlers/image_handler.py` | 图片消息变更前校验本次请求总积分 | user_id, content, params | None |
| `resolve_image_generation_settings` | `backend/services/handlers/image_request_settings.py` | 统一解析图片提交与计费参数 | params, has_image_urls | Dict[str, Any] |
| `VideoHandler.start` | `backend/services/handlers/video_handler.py` | 提交入口已原子准备的视频任务（缺少 prepared task 时关闭失败） | message_id, conversation_id, user_id, content, params, metadata | client_task_id |
| `_reset_message_for_retry` | `backend/api/routes/message.py` | 重置失败消息用于重试 | db, message_id, gen_type, model, params | Message |
| `_create_assistant_placeholder` | `backend/api/routes/message.py` | 创建助手消息占位符 | db, conversation_id, message_id, gen_type, model, params | Message |
| `handle_regenerate_single_operation` | `backend/api/routes/message_generation_helpers.py` | 单图重新生成操作（复用现有消息，仅更新指定 image_index） | db, body, user_id | dict |
| `resolve_generation_context` | `backend/api/routes/message_request_preparation.py` | 解析生成类型并注入请求位置上下文 | request, body | GenerationType |
| `preflight_image_request` | `backend/api/routes/message_request_preparation.py` | 用正式任务参数执行图片积分预检 | handler, user_id, content, params, model, operation | None |
| `prepare_generation_request` | `backend/api/routes/message_request_preparation.py` | 权限校验、图片预检和用户消息创建 | db, conversation_id, body, gen_type, user_id, org_id, dependencies | tuple |
| `resolve_existing_turn_anchor` | `backend/api/routes/message_turn_anchors.py` | retry/单图重生读取原 Turn；旧消息缺字段时回退到此前最近 user 消息 | db, conversation_id, assistant_message_id | tuple[input_message_id, turn_id] |
| `insert_task_with_turn_binding` | `backend/services/turn_binding.py` | 插入 task 后调用事务 RPC 固定输入和上下文基线，失败补偿删除 task | db, task_data, input_message_id, turn_id, execution_mode | None |
| `close_bound_turn` | `backend/services/turn_binding.py` | 统一调用关闭 Turn 的事务 RPC | db, conversation_id, task_id, output_message_id | RPC result |
| `create_wecom_turn_task` | `backend/services/wecom/turn_lifecycle.py` | 为企微同步生成创建正式 chat task 并绑定 Turn | handler, conversation/user/message/turn fields | task UUID |
| `complete_wecom_turn_task` | `backend/services/wecom/turn_lifecycle.py` | 企微生成成功后关闭 Turn 并完成 task | db, conversation_id, task_id, message_id, turn_id, org_id | None |
| `fail_wecom_turn_task` | `backend/services/wecom/turn_lifecycle.py` | 企微生成失败时只标记 task failed，不推进 revision | db, task_id, conversation_id, turn_id, error | None |
| `prepare_assistant_message` | `backend/api/routes/message_generation_helpers.py` | 按操作类型创建或重置助手消息 | db, conversation_id, body, gen_type | tuple |
| `finalize_image_request_failure` | `backend/api/routes/message_generation_helpers.py` | 将提交阶段失败持久化为失败图片快照 | db, message_id, operation, params, error_code, error_message | None |
| `find_task_in_connection_scope` | `backend/services/websocket_task_scope.py` | 按连接绑定的 user_id 与精确 org_id（含个人 null）查询可订阅任务 | db, task_id, user_id, org_id | dict \| None |
| `_handle_task_subscription` | `backend/api/routes/ws.py` | 校验任务租户边界后建立 WebSocket 订阅并恢复任务状态 | conn_id, user_id, org_id, task_id | None |
| `_handle_user_steer` | `backend/api/routes/ws.py` | 校验任务租户边界后处理执行中追加消息 | conn_id, user_id, org_id, task_id, message | None |
| `WebSocketManager.send_to_user` | `backend/services/websocket_manager.py` | 按用户与精确 org_id 投递；org_id=None 仅表示个人空间 | user_id, message, org_id | None |
| `WebSocketManager.send_to_task_subscribers` | `backend/services/websocket_manager.py` | 按 `(task_id, org_id)` 复合订阅键执行本地及 Redis 投递 | task_id, message, org_id | int |
| `WebSocketManager.send_to_task_or_user` | `backend/services/websocket_manager.py` | 优先按复合任务订阅投递，无订阅时按用户与精确 org_id 兜底 | task_id, user_id, message, org_id | None |
| `RedisPubSubMixin._deliver_from_redis` | `backend/services/websocket_redis.py` | 消费跨 Worker 消息并按精确企业上下文过滤本地连接 | data | None |
| `WebSocketInteractionMixin.wait_for_confirm` | `backend/services/websocket_interactions.py` | 按 tool_call_id、user_id、org_id 复合键等待工具确认 | tool_call_id, user_id, org_id, timeout | bool |
| `WebSocketInteractionMixin.resolve_confirm` | `backend/services/websocket_interactions.py` | 只解析同一用户与企业上下文的工具确认 | tool_call_id, user_id, org_id, approved | bool |
| `WebSocketInteractionMixin.resolve_steer` | `backend/services/websocket_interactions.py` | 按 task_id、org_id 复合键解析用户打断 | task_id, message, org_id | bool |

#### 前端函数

| 函数名 | 文件路径 | 功能描述 | 参数 | 返回值 |
|--------|----------|----------|------|--------|
| `sendMessage` | `frontend/src/services/messageSender.ts` | 使用固定请求标识和幂等键统一发送，并安全重试不确定结果 | options: SendOptions | Promise<string> |
| `createWSMessageHandlers` | `frontend/src/contexts/wsMessageHandlers.ts` | 创建 WebSocket 事件名到处理函数的统一映射 | deps | Record<string, handler> |
| `useWebSocket` | `frontend/src/hooks/useWebSocket.ts` | 使用当前认证组织建立连接；组织切换时清理旧连接并按新租户重连 | options | WebSocket API |
| `flushChunkBuffer` | `frontend/src/contexts/wsMessageHandlerShared.ts` | 将 16ms 窗口内累积的流式 chunk 批量写入 Store | deps | void |
| `handleMessageDone` | `frontend/src/contexts/wsTaskMessageHandlers.ts` | 处理任务最终消息、幂等完成、订阅清理与 toast | deps, msg | void |
| `handleMessageError` | `frontend/src/contexts/wsTaskMessageHandlers.ts` | 处理聊天/媒体失败状态、错误回调和订阅清理 | deps, msg | void |
| `handleImagePartialUpdate` | `frontend/src/contexts/wsTaskMessageHandlers.ts` | 原位替换多图批次的指定图片槽位 | deps, msg | void |
| `applyOptimisticUpdate` | `frontend/src/services/messageSendLifecycle.ts` | 创建用户乐观消息与助手占位状态 | options, ctx | void |
| `processApiResponse` | `frontend/src/services/messageSendLifecycle.ts` | 替换占位状态、创建任务追踪并校验 task_id | response, options, ctx | void |
| `rollbackOnError` | `frontend/src/services/messageSendLifecycle.ts` | 发送失败时恢复原消息或构造统一失败状态 | error, options, ctx | void |
| `getSendFailureDisposition` | `frontend/src/services/messageSendLifecycle.ts` | 将发送错误分类为明确拒绝、已记录失败或结果未知 | error | SendFailureDisposition |
| `useInputSubmission` | `frontend/src/components/chat/input/useInputSubmission.ts` | 消费统一附件快照，贯通聊天、图片、视频和电商模式，并按发送结果结算草稿 | options | handlers |
| `useChatAttachments` | `frontend/src/components/chat/attachments/useChatAttachments.ts` | 统一上传、引用、工作区附件的添加、删除、派生状态和草稿事务 | - | ChatAttachmentController |
| `createAttachmentSubmissionSnapshot` | `frontend/src/components/chat/attachments/attachmentSubmission.ts` | 将统一附件转换为原图输入、图片元数据和文件提交结构 | attachments | AttachmentSubmissionSnapshot |
| `ChatAttachmentPreview` | `frontend/src/components/chat/attachments/ChatAttachmentPreview.tsx` | 所有图片显示统一缩略图，普通文件显示文件卡片，并按附件 ID 删除 | attachments, onRemove | JSX |
| `useInputDraftTransaction` | `frontend/src/components/chat/input/useInputDraftTransaction.ts` | 管理文本草稿的立即移出、明确拒绝合并恢复与引用文字监听 | options | draft transaction handlers |
| `detachImagesForSubmission` | `frontend/src/hooks/useImageUpload.ts` | 提交时移出图片并返回可合并恢复函数 | - | restore function |
| `detachFilesForSubmission` | `frontend/src/hooks/useFileUpload.ts` | 提交时移出文件并返回可合并恢复函数 | - | restore function |
| `useInputTaskControls` | `frontend/src/components/chat/input/useInputTaskControls.ts` | 停止当前任务、ESC 中断和 steer 信号发送 | options | handlers |
| `useInputExternalEvents` | `frontend/src/components/chat/input/useInputExternalEvents.ts` | 注册并清理电商确认、建议发送窗口事件 | options | void |
| `toApiRequestError` | `frontend/src/services/api.ts` | 提取结构化业务错误并区分 HTTP、超时与网络故障 | error: unknown | ApiRequestError |
| `getMessages` | `frontend/src/services/message.ts` | 获取消息列表 | conversationId, limit, offset, beforeId | Promise<MessageListResponse> |
| `deleteMessage` | `frontend/src/services/message.ts` | 删除单条消息 | messageId | Promise<DeleteMessageResponse> |
| `handleRegenerate` | `frontend/src/hooks/useRegenerateHandlers.ts` | 处理消息重新生成/重试请求 | targetMessage, userMessage | Promise<void> |
| `handleChatMessage` | `frontend/src/hooks/handlers/useTextMessageHandler.ts` | 处理聊天消息发送 | messageContent, conversationId, imageUrl | Promise<void> |
| `handleMediaGeneration` | `frontend/src/hooks/handlers/useMediaMessageHandler.ts` | 处理媒体生成请求 | conversationId, prompt, imageUrl | Promise<void> |

### 图像生成模块 (Image Generation)

#### 后端函数

| 函数名 | 文件路径 | 功能描述 | 参数 | 返回值 |
|--------|----------|----------|------|--------|
| `generate_image` | `backend/services/image_service.py` | 生成图像 | user_id, prompt, model, size, resolution, wait_for_result | Dict[str, Any] |
| `edit_image` | `backend/services/image_service.py` | 编辑图像 | user_id, prompt, image_urls, size, wait_for_result | Dict[str, Any] |
| `query_task` | `backend/services/image_service.py` | 查询图像任务状态 | task_id | Dict[str, Any] |

#### 前端函数

| 函数名 | 文件路径 | 功能描述 | 参数 | 返回值 |
|--------|----------|----------|------|--------|
| `generateImage` | `frontend/src/services/image.ts` | 生成图像 | data: GenerateImageRequest | Promise<GenerateImageResponse> |
| `editImage` | `frontend/src/services/image.ts` | 编辑图像 | data: EditImageRequest | Promise<GenerateImageResponse> |
| `uploadImage` | `frontend/src/services/image.ts` | 上传图片到存储服务 | imageData: string | Promise<UploadImageResponse> |
| `queryTaskStatus` | `frontend/src/services/image.ts` | 查询任务状态 | taskId: string | Promise<TaskStatusResponse> |
| `pollTaskUntilDone` | `frontend/src/services/image.ts` | 轮询任务直到完成 | taskId, options | Promise<TaskStatusResponse> |

### 用户设置模块 (User Settings)

#### 前端函数

| 函数名 | 文件路径 | 功能描述 | 参数 | 返回值 |
|--------|----------|----------|------|--------|
| `getSavedSettings` | `frontend/src/utils/settingsStorage.ts` | 从 localStorage 加载用户保存的设置 | - | UserAdvancedSettings |
| `saveSettings` | `frontend/src/utils/settingsStorage.ts` | 保存用户设置到 localStorage | settings: UserAdvancedSettings | void |
| `resetSettings` | `frontend/src/utils/settingsStorage.ts` | 重置为默认设置并清除 localStorage | - | UserAdvancedSettings |

### 视频生成模块 (Video Generation)

#### 后端函数

| 函数名 | 文件路径 | 功能描述 | 参数 | 返回值 |
|--------|----------|----------|------|--------|
| `generate_text_to_video` | `backend/services/video_service.py` | 文本生成视频 | user_id, prompt, model, n_frames, aspect_ratio, remove_watermark, wait_for_result | Dict[str, Any] |
| `generate_image_to_video` | `backend/services/video_service.py` | 图片生成视频 | user_id, prompt, image_url, model, n_frames, aspect_ratio, remove_watermark, wait_for_result | Dict[str, Any] |
| `generate_storyboard_video` | `backend/services/video_service.py` | 故事板视频生成 | user_id, model, n_frames, storyboard_images, aspect_ratio, wait_for_result | Dict[str, Any] |
| `query_task` | `backend/services/video_service.py` | 查询视频任务状态 | task_id | Dict[str, Any] |
| `generate` | `backend/services/adapters/kie/video_adapter.py` | KIE 视频生成适配器 | prompt, image_urls, n_frames, aspect_ratio, remove_watermark, wait_for_result | Dict[str, Any] |

#### 前端函数

| 函数名 | 文件路径 | 功能描述 | 参数 | 返回值 |
|--------|----------|----------|------|--------|
| `generateTextToVideo` | `frontend/src/services/video.ts` | 文本生成视频 | data: GenerateTextToVideoRequest | Promise<GenerateVideoResponse> |
| `generateImageToVideo` | `frontend/src/services/video.ts` | 图片生成视频 | data: GenerateImageToVideoRequest | Promise<GenerateVideoResponse> |
| `generateStoryboardVideo` | `frontend/src/services/video.ts` | 故事板视频生成 | data: GenerateStoryboardVideoRequest | Promise<GenerateVideoResponse> |
| `queryVideoTaskStatus` | `frontend/src/services/video.ts` | 查询视频任务状态 | taskId: string | Promise<TaskStatusResponse> |
| `pollVideoTaskUntilDone` | `frontend/src/services/video.ts` | 轮询视频任务直到完成 | taskId, options | Promise<TaskStatusResponse> |
| `handleVideoGeneration` | `frontend/src/components/chat/InputArea.tsx` | 处理视频生成请求 | messageContent, currentConversationId, imageUrl | Promise<void> |

### Webhook 回调与任务完成服务模块 (Webhook & Task Completion)

> **新增于 Webhook 回调改造**：将图片/视频任务从纯轮询改为「回调为主 + 轮询兜底」，统一完成处理入口。

#### 后端函数

| 函数名 | 文件路径 | 功能描述 | 参数 | 返回值 |
|--------|----------|----------|------|--------|
| `_track_processing_task` | `backend/api/routes/webhook.py` | 托管 Webhook 后台任务强引用并记录异常/兜底 | task, provider, external_task_id | None |
| `_is_authorized_callback` | `backend/api/routes/webhook.py` | 使用常量时间比较验证 Webhook Token | request | bool |
| `_start_processing` | `backend/api/routes/webhook.py` | 立即启动并托管统一任务完成处理 | service, provider, external_task_id, result | None |
| `handle_webhook` | `backend/api/routes/webhook.py` | 验证 Token、按 provider 解析回调并立即启动后台处理 | provider: str, request, db | JSONResponse |
| `TaskCompletionService.__init__` | `backend/services/task_completion_service.py` | 初始化统一任务完成服务 | db: Client | - |
| `TaskCompletionService.get_task` | `backend/services/task_completion_service.py` | 根据 external_task_id 查询任务 | external_task_id: str | Optional[Dict] |
| `TaskCompletionService.process_result` | `backend/services/task_completion_service.py` | 统一处理入口（Redis 互斥 + DB 幂等） | external_task_id, result: TaskResult | bool |
| `TaskCompletionService._renew_completion_lock` | `backend/services/task_completion_service.py` | 定期续期媒体完成处理锁 | lock_key, lock_token | None |
| `TaskCompletionService._process_result_locked` | `backend/services/task_completion_service.py` | 持有分布式锁时执行原有成功/失败分流 | external_task_id, result | bool |
| `TaskCompletionService._handle_success` | `backend/services/task_completion_service.py` | 处理成功结果（OSS 上传 → handler.on_complete） | task, result | bool |
| `TaskCompletionService._handle_failure` | `backend/services/task_completion_service.py` | 处理失败结果（handler.on_error） | task, result | bool |
| `TaskCompletionService._upload_urls_to_oss` | `backend/services/task_completion_service.py` | 批量上传媒体到 OSS（降级返回原 URL） | urls, user_id, task_type | List[str] |
| `TaskCompletionService._build_content_parts` | `backend/services/task_completion_service.py` | 构建 ContentPart 字典列表 | urls, task_type | list |
| `TaskCompletionService._create_handler` | `backend/services/task_completion_service.py` | 根据任务类型创建 Handler | task_type: str | BaseHandler |
| `_empty_result` | `backend/services/task_completion_service.py` | 将成功结果转为失败结果（空结果场景） | original, fail_code, fail_msg | TaskResult |
| `BaseImageAdapter.extract_task_id` | `backend/services/adapters/base.py` | 从回调 payload 提取任务 ID（抽象方法） | payload: Dict | str |
| `BaseImageAdapter.parse_callback` | `backend/services/adapters/base.py` | 解析回调 payload 为 ImageGenerateResult（抽象方法） | payload: Dict | ImageGenerateResult |
| `BaseVideoAdapter.extract_task_id` | `backend/services/adapters/base.py` | 从回调 payload 提取任务 ID（抽象方法） | payload: Dict | str |
| `BaseVideoAdapter.parse_callback` | `backend/services/adapters/base.py` | 解析回调 payload 为 VideoGenerateResult（抽象方法） | payload: Dict | VideoGenerateResult |
| `KieImageAdapter.extract_task_id` | `backend/services/adapters/kie/image_adapter.py` | KIE 图片回调提取 taskId | payload: Dict | str |
| `KieImageAdapter.parse_callback` | `backend/services/adapters/kie/image_adapter.py` | 解析 KIE 图片回调（taskId+state+resultJson） | payload: Dict | ImageGenerateResult |
| `KieVideoAdapter.extract_task_id` | `backend/services/adapters/kie/video_adapter.py` | KIE 视频回调提取 taskId | payload: Dict | str |
| `KieVideoAdapter.parse_callback` | `backend/services/adapters/kie/video_adapter.py` | 解析 KIE 视频回调（taskId+state+resultJson） | payload: Dict | VideoGenerateResult |
| `extract_callback_data` | `backend/services/adapters/kie/models.py` | 校验并解包 KIE Market 统一 `{code,msg,data}` 回调信封 | payload: Dict | Dict |
| `BaseHandler._build_callback_url` | `backend/services/handlers/base.py` | 构建带 Token 的 Webhook URL（base/token 任一未配置则返回 None） | provider_value: str | Optional[str] |
| `BatchCompletionService.handle_image_complete` | `backend/services/batch_completion_service.py` | 处理单个图片 task 成功（确认积分、推送 partial update、finalize） | task, content_parts | bool |
| `BatchCompletionService.handle_image_failure` | `backend/services/batch_completion_service.py` | 处理单个图片 task 失败（退回积分、推送 partial update、finalize） | task, error_code, error_message | bool |
| `BatchCompletionService._dispatch_finalize` | `backend/services/batch_completion_service.py` | 根据操作类型分发到 _finalize_batch 或 _finalize_single_image | batch_id, batch_tasks | None |
| `BatchCompletionService._finalize_single_image` | `backend/services/batch_completion_service.py` | 兼容代理：调用 BatchMessageFinalizer 合并单图重生结果 | batch_id, batch_tasks | None |
| `BatchCompletionService._finalize_batch` | `backend/services/batch_completion_service.py` | 兼容代理：调用 BatchMessageFinalizer 汇总批次消息 | batch_id, batch_tasks | None |
| `BatchMessageFinalizer.finalize_single_image` | `backend/services/batch_message_finalizer.py` | 单图重新生成最终处理（merge-update 目标槽位、message_done、释放槽位） | batch_id, batch_tasks | None |
| `BatchMessageFinalizer.finalize_batch` | `backend/services/batch_message_finalizer.py` | 批次终态汇总（upsert 消息、message_done、对话预览、释放槽位） | batch_id, batch_tasks | None |

### KIE 适配器模块 (KIE Adapter)

#### 后端函数

| 函数名 | 文件路径 | 功能描述 | 参数 | 返回值 |
|--------|----------|----------|------|--------|
| `chat` | `backend/services/adapters/kie/chat_adapter.py` | 发送聊天请求（支持流式/非流式） | messages, stream, tools, etc | ChatCompletionChunk or AsyncIterator |
| `chat_simple` | `backend/services/adapters/kie/chat_adapter.py` | 简化聊天接口 | user_message, system_prompt, history, stream | ChatCompletionChunk or AsyncIterator |
| `estimate_cost` | `backend/services/adapters/kie/chat_adapter.py` | 估算积分消耗 | input_tokens, output_tokens | CostEstimate |
| `KieClient._handle_error_response` | `backend/services/adapters/kie/client.py` | 分类 KIE 错误并记录余额不足告警事件 | status_code, response_data, model | NoReturn |
| `chat_completions` | `backend/services/adapters/kie/client.py` | 非流式 Chat API | model, request | ChatCompletionChunk |
| `chat_completions_stream` | `backend/services/adapters/kie/client.py` | 流式 Chat API（SSE） | model, request | AsyncIterator[ChatCompletionChunk] |

#### 前端函数

> **重构说明**：原 `useChatStore`、`useTaskStore`、`useConversationRuntimeStore` 已合并为统一的 `useMessageStore`。

| 函数名 | 文件路径 | 功能描述 | 参数 | 返回值 |
|--------|----------|----------|------|--------|
| `useMessageStore` | `frontend/src/stores/useMessageStore.ts` | 统一消息状态管理（消息、任务、缓存） | - | MessageStore |
| `addMessage` | `frontend/src/stores/useMessageStore.ts` | 添加消息 | conversationId, message | void |
| `updateMessage` | `frontend/src/stores/useMessageStore.ts` | 更新消息 | messageId, updates | void |
| `removeMessage` | `frontend/src/stores/useMessageStore.ts` | 删除消息 | messageId | void |
| `setMessagesForConversation` | `frontend/src/stores/useMessageStore.ts` | 设置对话消息 | conversationId, messages, hasMore | void |
| `startChatTask` | `frontend/src/stores/useMessageStore.ts` | 开始聊天任务 | conversationId, title | void |
| `completeChatTask` | `frontend/src/stores/useMessageStore.ts` | 完成聊天任务 | conversationId | void |
| `startMediaTask` | `frontend/src/stores/useMessageStore.ts` | 开始媒体任务 | options | void |
| `completeMediaTask` | `frontend/src/stores/useMessageStore.ts` | 完成媒体任务 | taskId, result | void |
| `canStartTask` | `frontend/src/stores/useMessageStore.ts` | 检查是否可以开始新任务 | conversationId | { allowed, reason? } |
| `hasActiveTask` | `frontend/src/stores/useMessageStore.ts` | 检查对话是否有活跃任务 | conversationId | boolean |
| `getTextContent` | `frontend/src/stores/useMessageStore.ts` | 从 Message 提取文本内容 | message | string |
| `normalizeMessage` | `frontend/src/stores/useMessageStore.ts` | 标准化消息格式（兼容旧格式） | message | Message |
| `formatDateGroup` | `frontend/src/components/chat/conversationUtils.ts` | 格式化日期分组（今天/昨天/具体日期） | dateStr | string |
| `groupConversationsByDate` | `frontend/src/components/chat/conversationUtils.ts` | 按日期分组对话列表 | conversations | Record |

### 自定义 Hooks 模块 (Custom Hooks)

#### 前端函数

| 函数名 | 文件路径 | 功能描述 | 参数 | 返回值 |
|--------|----------|----------|------|--------|
| `useClickOutside` | `frontend/src/hooks/useClickOutside.ts` | 点击外部关闭逻辑 | ref, isVisible, onClose, skipCondition | void |
| `useMessageLoader` | `frontend/src/hooks/useMessageLoader.ts` | 消息加载（含缓存） | options | { messages, loading, loadMessages, ... } |
| `useMessageHandlers` | `frontend/src/hooks/useMessageHandlers.ts` | 消息发送处理 | options | { handleSendMessage, isProcessing, ... } |
| `useRegenerateHandlers` | `frontend/src/hooks/useRegenerateHandlers.ts` | 消息重新生成处理 | options | { handleRegenerate, isRegenerating, ... } |
| `useModelSelection` | `frontend/src/hooks/useModelSelection.ts` | 模型选择逻辑（含 hasQuotedImage 自动切换编辑模型） | options | { selectedModel, setSelectedModel, ... } |
| `useImageUpload` | `frontend/src/hooks/useImageUpload.ts` | 图片上传逻辑（含引用图片 addQuotedImage/hasQuotedImage） | - | { uploadImage, uploading, addQuotedImage, hasQuotedImage, ... } |
| `useAudioRecording` | `frontend/src/hooks/useAudioRecording.ts` | 录音逻辑 | - | { startRecording, stopRecording, ... } |
| `useDragDropUpload` | `frontend/src/hooks/useDragDropUpload.ts` | 拖拽上传逻辑 | - | { isDragging, handleDrop, ... } |
| `useVirtuaScroll` | `frontend/src/hooks/useVirtuaScroll.ts` | Virtua 滚动管理（统一入口） | options | { vlistRef, scrollToBottom, ... } |

### 通用组件模块 (Common Components)

#### 前端组件

| 组件名 | 文件路径 | 功能描述 |
|--------|----------|----------|
| `Modal` | `frontend/src/components/common/Modal.tsx` | 通用弹窗组件（动画、ESC关闭、遮罩层点击关闭、防止背景滚动） |

### 认证弹窗模块 (Auth Modal)

#### 前端组件

| 组件名 | 文件路径 | 功能描述 |
|--------|----------|----------|
| `AuthModal` | `frontend/src/components/auth/AuthModal.tsx` | 认证弹窗容器，整合登录/注册表单，根据 mode 切换显示 |
| `LoginForm` | `frontend/src/components/auth/LoginForm.tsx` | 登录表单组件，支持密码登录和验证码登录双模式 |
| `RegisterForm` | `frontend/src/components/auth/RegisterForm.tsx` | 注册表单组件，手机号+验证码注册 |
| `ProtectedRoute` | `frontend/src/components/auth/ProtectedRoute.tsx` | 路由守卫组件，未登录时弹出认证弹窗 |

#### 前端函数

| 函数名 | 文件路径 | 功能描述 | 参数 | 返回值 |
|--------|----------|----------|------|--------|
| `useAuthModalStore` | `frontend/src/stores/useAuthModalStore.ts` | Zustand 认证弹窗状态管理 | - | AuthModalStore |
| `open` | `frontend/src/stores/useAuthModalStore.ts` | 打开认证弹窗 | mode: 'login' \| 'register' | void |
| `close` | `frontend/src/stores/useAuthModalStore.ts` | 关闭认证弹窗 | - | void |
| `switchMode` | `frontend/src/stores/useAuthModalStore.ts` | 切换登录/注册模式 | - | void |

### 聊天组件模块 (Chat Components)

#### 前端组件

| 组件名 | 文件路径 | 功能描述 |
|--------|----------|----------|
| `Chat` | `frontend/src/pages/Chat.tsx` | 聊天主页面，管理侧边栏、消息区域、输入区域 |
| `Sidebar` | `frontend/src/components/chat/Sidebar.tsx` | 左侧栏，包含新建对话、对话列表、用户菜单 |
| `ConversationList` | `frontend/src/components/chat/ConversationList.tsx` | 对话列表主组件（302行） |
| `ConversationItem` | `frontend/src/components/chat/ConversationItem.tsx` | 单个对话项组件 |
| `ContextMenu` | `frontend/src/components/chat/ContextMenu.tsx` | 右键菜单组件 |
| `DeleteConfirmModal` | `frontend/src/components/chat/DeleteConfirmModal.tsx` | 对话删除确认弹框 |
| `MessageArea` | `frontend/src/components/chat/MessageArea.tsx` | 消息区域，显示对话消息 |
| `MessageItem` | `frontend/src/components/chat/message/MessageItem.tsx` | 单条消息编排（memo 包裹，useCallback 稳定回调：handleImageClick/handleRegenerateSingle/handleRegenerate） |
| `MessageBubbleContent` | `frontend/src/components/chat/message/MessageBubbleContent.tsx` | 气泡内容分发：加载、取消、错误、Markdown、多内容块 |
| `MessageContentBlocks` | `frontend/src/components/chat/message/MessageContentBlocks.tsx` | AI 多内容块渲染：thinking/tool/text/image/file/chart/table/form/ecom_plan |
| `MarkdownRenderer` | `frontend/src/components/chat/message/MarkdownRenderer.tsx` | 普通文本快速渲染；检测到 Markdown 或公式语法后按需加载富文本引擎 | content, isStreaming?, className? | React element |
| `RichMarkdownRenderer` | `frontend/src/components/chat/message/RichMarkdownRenderer.tsx` | Markdown、GFM、KaTeX、代码高亮、Mermaid 与安全文件链接的重型渲染实现 | content, isStreaming?, className? | React element |
| `MessageMedia` | `frontend/src/components/chat/message/MessageMedia.tsx` | 消息媒体容器（图片、视频、文件、生成占位符） |
| `MessageImageBlocks` | `frontend/src/components/chat/message/MessageImageBlocks.tsx` | 图片块渲染：小图用 thumbnailUrl，下载/菜单用 originalUrl |
| `InlineChartImage` | `frontend/src/components/chat/message/InlineChartImage.tsx` | 内容块内联图片，固定占位并按缩略图规则展示 |
| `MessageActions` | `frontend/src/components/chat/MessageActions.tsx` | 消息操作工具栏（复制、朗读、反馈、分享、删除） |
| `MessageToolbar` | `frontend/src/components/chat/MessageToolbar.tsx` | 消息工具栏（旧版） |
| `DeleteMessageModal` | `frontend/src/components/chat/DeleteMessageModal.tsx` | 删除消息确认弹框 |
| `InputArea` | `frontend/src/components/chat/InputArea.tsx` | 输入区域 |
| `InputControls` | `frontend/src/components/chat/InputControls.tsx` | 输入控制（文本框、按钮、上传） |
| `ModelSelector` | `frontend/src/components/chat/ModelSelector.tsx` | 模型选择器 |
| `AdvancedSettingsMenu` | `frontend/src/components/chat/AdvancedSettingsMenu.tsx` | 高级设置菜单 |
| `SettingsModal` | `frontend/src/components/chat/SettingsModal.tsx` | 个人设置弹框 |
| `UploadMenu` | `frontend/src/components/chat/UploadMenu.tsx` | 上传菜单 |
| `ImagePreviewModal` | `frontend/src/components/chat/ImagePreviewModal.tsx` | 图片预览弹窗（全屏缩放下载） |
| `LoadingPlaceholder` | `frontend/src/components/chat/LoadingPlaceholder.tsx` | 统一加载占位符（文字 + 跳动小圆点） |
| `MediaPlaceholder` / `FailedMediaPlaceholder` | `frontend/src/components/chat/media/MediaPlaceholder.tsx` | 统一媒体占位符；失败状态支持错误码、积分不足警告和重新生成 |
| `ImageContextMenu` | `frontend/src/components/chat/ImageContextMenu.tsx` | 图片右键上下文菜单；引用操作调用统一附件 Context 命令 |
| `AiImageGrid` | `frontend/src/components/chat/media/AiImageGrid.tsx` | AI 多图网格组件；仅消费 ImagePart，生成中补请求占位，完成后只展示实际图片 |
| `GridCell` | `frontend/src/components/chat/media/AiImageGrid.tsx` | 单个网格单元（memo + gridCellAreEqual 自定义比较，仅数据 props 变化时重渲染） |
| `gridCellAreEqual` | `frontend/src/components/chat/media/AiImageGrid.tsx` | GridCell 自定义 memo 比较函数（包括 errorCode，忽略函数引用） |
| `AudioPreview` | `frontend/src/components/chat/AudioPreview.tsx` | 音频预览 |
| `AudioRecorder` | `frontend/src/components/chat/AudioRecorder.tsx` | 录音组件 |
| `ConflictAlert` | `frontend/src/components/chat/ConflictAlert.tsx` | 模型冲突提示 |
| `EmptyState` | `frontend/src/components/chat/EmptyState.tsx` | 空状态提示 |
| `LoadingSkeleton` | `frontend/src/components/chat/LoadingSkeleton.tsx` | 加载骨架屏 |

### 工具函数模块 (Utility Functions)

#### 前端函数

| 函数名 | 文件路径 | 功能描述 | 参数 | 返回值 |
|--------|----------|----------|------|--------|
| `getSavedSettings` | `frontend/src/utils/settingsStorage.ts` | 从 localStorage 加载设置 | - | UserAdvancedSettings |
| `saveSettings` | `frontend/src/utils/settingsStorage.ts` | 保存设置到 localStorage | settings | void |
| `resetSettings` | `frontend/src/utils/settingsStorage.ts` | 重置为默认设置 | - | UserAdvancedSettings |
| `checkModelConflict` | `frontend/src/utils/modelConflict.ts` | 检查模型冲突 | model, hasImage, hasVideo | ConflictResult |
| `createTempMessage` | `frontend/src/utils/messageFactory.ts` | 创建临时消息 | content, role | Message |
| `createStreamingMessage` | `frontend/src/utils/messageFactory.ts` | 创建流式消息占位 | - | Message |
| `createMediaTimestamps` | `frontend/src/utils/messageFactory.ts` | 生成媒体消息时间戳和占位符ID | - | MediaTimestamps |
| `createMediaOptimisticPair` | `frontend/src/utils/messageFactory.ts` | 创建媒体生成乐观消息对 | conversationId, content, imageUrl, loadingText, timestamps | { userMessage, placeholder } |
| `getPlaceholderText` | `frontend/src/constants/placeholder.ts` | 获取占位符文字（聊天/媒体通用） | type | string |
| `getPlaceholderInfo` | `frontend/src/constants/placeholder.ts` | 判断是否为占位符消息 | message | PlaceholderInfo |
| `isMediaPlaceholder` | `frontend/src/constants/placeholder.ts` | 判断是否为媒体占位符 | message | boolean |
| `getMediaPlaceholderLabel` | `frontend/src/components/chat/MediaPlaceholder.tsx` | 获取媒体占位符标签文字 | type | string |
| `regenerateMessage` | `frontend/src/utils/regenerate/index.ts` | 统一重新生成入口（自动判断失败/成功） | options | Promise<void> |
| `regenerateInPlace` | `frontend/src/utils/regenerate/regenerateInPlace.ts` | 失败消息原地重新生成 | options | Promise<void> |
| `regenerateChatInPlace` | `frontend/src/utils/regenerate/strategies/chatStrategy.ts` | 聊天消息原地重新生成策略 | options | Promise<void> |
| `regenerateImageInPlace` | `frontend/src/utils/regenerate/strategies/imageStrategy.ts` | 图片消息原地重新生成策略（复用 executeImageGenerationCore） | RegenerateImageInPlaceOptions | Promise<void> |
| `regenerateVideoInPlace` | `frontend/src/utils/regenerate/strategies/videoStrategy.ts` | 视频消息原地重新生成策略（复用 executeVideoGenerationCore） | RegenerateVideoInPlaceOptions | Promise<void> |
| `findMessagePair` | `frontend/src/components/chat/MessageArea.tsx` | 查找 AI 消息及其对应的用户消息（重新生成用） | messageId | { target, user } \| null |

### 任务通知模块 (Task Notification)

> **新增于阶段4重构**：提取任务完成通知逻辑为纯函数，消除 useMessageStore 中的重复代码。

#### 前端函数

| 函数名 | 文件路径 | 功能描述 | 参数 | 返回值 |
|--------|----------|----------|------|--------|
#### 共享类型定义

| 类型名 | 文件路径 | 功能描述 |
|--------|----------|----------|
| `StoreTaskStatus` | `frontend/src/types/task.ts` | Store 任务状态（pending、streaming、polling、completed、error） |
| `StoreTaskType` | `frontend/src/types/task.ts` | Store 任务类型（chat、image、video） |
| `CompletedNotification` | `frontend/src/types/task.ts` | 完成通知接口（id、conversationId、type、completedAt、isRead） |

---

### 统一日志工具模块 (Logger)

> **新增于阶段0重构**：提供格式化的日志输出，支持业务上下文。

#### 前端函数

| 函数名 | 文件路径 | 功能描述 | 参数 | 返回值 |
|--------|----------|----------|------|--------|
| `logger.error` | `frontend/src/utils/logger.ts` | 错误日志（带业务上下文） | scope, message, error?, context? | void |
| `logger.warn` | `frontend/src/utils/logger.ts` | 警告日志 | scope, message, context? | void |
| `logger.debug` | `frontend/src/utils/logger.ts` | 调试日志（仅开发环境） | scope, message, data? | void |
| `logger.info` | `frontend/src/utils/logger.ts` | 信息日志 | scope, message, context? | void |

---

### 任务协调器模块 (Task Coordinator)

> **用于多标签页任务轮询协调**：通过 BroadcastChannel 和 localStorage 锁机制防止重复轮询。

#### 前端函数

| 函数名 | 文件路径 | 功能描述 | 参数 | 返回值 |
|--------|----------|----------|------|--------|
| `taskCoordinator.canStartPolling` | `frontend/src/utils/taskCoordinator.ts` | 检查是否可以开始轮询（获取锁） | taskId: string | boolean |
| `taskCoordinator.releasePolling` | `frontend/src/utils/taskCoordinator.ts` | 释放轮询锁 | taskId: string | void |
| `taskCoordinator.renewLock` | `frontend/src/utils/taskCoordinator.ts` | 续约锁（每15秒调用） | taskId: string | void |
| `taskCoordinator.cleanup` | `frontend/src/utils/taskCoordinator.ts` | 清理所有锁（页面卸载时） | - | void |

---

### 消息合并工具模块 (Merge Optimistic Messages)

> **用于合并持久化消息和乐观更新消息**：处理去重、temp-消息替换、streaming-消息替换等场景。

#### 前端函数

| 函数名 | 文件路径 | 功能描述 | 参数 | 返回值 |
|--------|----------|----------|------|--------|
| `mergeOptimisticMessages` | `frontend/src/utils/mergeOptimisticMessages.ts` | 合并持久化消息和乐观更新消息 | persistedMessages, runtimeState | Message[] |

---

### 记忆模块 (Memory Module)

> 通用 Session Memory、Consolidation、Curated Memory 与 Search/Get。

#### 后端函数

| 函数名 | 文件路径 | 功能描述 | 参数 | 返回值 |
|--------|----------|----------|------|--------|
| `parse_memory_candidate` | `backend/services/memory/contracts.py` | 严格解析通用记忆候选、原文证据与有效期 | raw | MemoryCandidate |
| `validate_memory_candidate` | `backend/services/memory/candidate_validator.py` | fail-closed 校验候选类型、时效、用户消息来源和精确原文引用 | candidate, messages | MemoryValidationResult |
| `_parse_extraction_result` | `backend/services/memory/l1_extractor.py` | 严格解析 `NO_MEMORY/CANDIDATES`，整批执行原文证据门禁并映射迁移期旧表协议 | raw, source_messages | List[SceneSegment] |
| `L1Extractor.propose` | `backend/services/memory/l1_extractor.py` | 生成并验证无数据库副作用的通用 Flush 提议，明确区分 NO_MEMORY 与非法输出 | messages, background?, previous_scene_name? | L1ProposalResult |
| `SessionFlushService.flush` | `backend/services/memory/session_flush.py` | 对固定闭合 revision 执行单进程 single-flight、20 消息窗口、Session Log 与 cursor CAS 提交 | user_id, org_id, conversation_id, through_revision, trigger? | SessionFlushResult |
| `get_embedding` / `get_embeddings` | `backend/services/memory/embedding.py` | 单条兼容与批量向量生成；批次结果缺失或 Provider 失败时整批返回 None | text / texts | embedding / embeddings / None |
| `MemoryConsolidator.consolidate` | `backend/services/memory/consolidator.py` | 对至少 3 份新 Session Log 执行同用户 singleflight、证据复验、受限关系判断、批量 embedding 与原子 Curated 晋升 | user_id, org_id | ConsolidationResult |
| `format_consolidation_prompt` | `backend/services/memory/prompts/consolidation.py` | 将 Session 候选与现有 Curated Memory 作为不可信 JSON 数据传给只读关系分类器 | session_candidates, curated_memories | str |
| `normalize_relevance` / `rank_for_recall` | `backend/services/memory/recall_policy.py` | 归一向量/BM25 相关性，执行硬阈值、时间衰减和字符 MMR 多样化 | channel scores / candidates, max_results, threshold | float / ranked candidates |
| `RetrievalPipeline.search` / `get` | `backend/services/memory/retrieval_pipeline.py` | 混合搜索或按 ID 严格读取 active 且处于有效期内的租户隔离 Curated Memory；Runtime 仅返回通用 kind，不读取旧 type/scene | query or atom_id, user_id, org_id | List[ScoredMemory] / ScoredMemory / None |
| `MemoryServiceV2.get_relevant_memories` | `backend/services/memory/memory_service_v2.py` | 按用户与组织范围执行通用 Curated Memory 召回，仅输出 kind、优先级与相关性元数据 | user_id, query, limit, org_id | List[Dict] |
| `MemoryServiceV2.get_memory` | `backend/services/memory/memory_service_v2.py` | Facade 层严格 Get，并返回 kind、有效期和原始消息溯源 | user_id, memory_id, org_id | Dict / None |
| `build_memory_tools` | `backend/config/memory_tools.py` | 构建个人上下文范围内只读 memory_search/memory_get Schema | — | List[ToolSchema] |
| `MemoryToolMixin._memory_search` / `_memory_get` | `backend/services/agent/memory_tool_mixin.py` | 在 ToolExecutor 用户/组织范围内主动搜索或按稳定 ref 获取 Curated Memory | args | AgentResult |
| `PromptBuilder._refresh_memory_after_compaction` | `backend/services/prompt_builder/builder.py` | Context Compaction 后失效旧 Session Memory cache 并按当前问题重新检索，失败返回空 | — | Tuple[memory, persona] |
| `PipelineScheduler._run_l1` | `backend/services/memory/pipeline_scheduler.py` | 仅对闭合 revision 执行 Session Flush→Consolidation；缺失 revision 时失败关闭且不写状态 | state, through_revision? | None |
| `MemoryServiceV2.build_memory_context` | `backend/services/memory/memory_service_v2.py` | 构建最多 3 条 Curated Memory 上下文；第二个兼容返回值固定为空，不再读取 L3 Persona | user_id, org_id, query | Tuple[memory, empty] |
| `MemoryServiceV2.get_session_memory_shadow` | `backend/services/memory/memory_service_v2.py` | 只读最近 Session Memory 候选供对账，不参与 Prompt 注入 | user_id, limit? | List[str] |
| `commit_memory_session_flush` | `backend/migrations/141_memory_session_flush_cas.sql` | 锁定 pipeline state，原子写入 Session Log 并以 expected revision 推进 cursor | org_id, user_id, conversation_id, expected_revision, through_revision, trigger, content, source_refs, content_hash, model, prompt_version | JSONB |
| `commit_memory_consolidation` | `backend/migrations/143_memory_consolidation_commit.sql` | 锁定 Session 来源和相关 Curated Atom，原子提交晋升、生命周期关系、Run 与来源消费 | org_id, user_id, source_log_ids, source_hash, operations, model, prompt_version, receipt | JSONB |
| `ChatContextMixin._build_llm_messages` | `backend/services/handlers/chat_context_mixin.py` | 按固定快照组装模型上下文，并将请求级模型与组织范围传入统一 PromptBuilder | content, user_id, conversation_id, text_content, prefetched_summary?, user_location?, permission_mode, context_anchor?, model_id?, org_id? | List[Dict] |
| `ChatContextMixin._extract_memories_async` | `backend/services/handlers/chat_context_mixin.py` | 异步提取记忆并向调度器传递已提交的输入/输出消息证据 ID 与闭合 revision | user_id, conversation_id, user_text, assistant_text, input_message_id?, output_message_id?, through_revision? | None |

#### 前端函数

| 函数名 | 文件路径 | 功能描述 | 参数 | 返回值 |
|--------|----------|----------|------|--------|
| `useMemoryStore` | `frontend/src/stores/useMemoryStore.ts` | Zustand 记忆状态管理（CRUD + 设置） | - | MemoryStore |
| `getMemories` | `frontend/src/services/memory.ts` | 获取记忆列表 | - | Promise |
| `addMemory` | `frontend/src/services/memory.ts` | 添加记忆 | content | Promise |

#### 配置常量

| 常量名 | 值 | 文件路径 | 说明 |
|--------|-----|----------|------|

### 模型动态评分模块 (Model Scoring)

> **新增于 Agent 自主知识库 — 动态评分**：每小时从 knowledge_metrics 聚合模型表现，EMA 平滑评分后写入 knowledge_nodes，路由自动参考。

#### 后端函数

| 函数名 | 文件路径 | 功能描述 | 参数 | 返回值 |
|--------|----------|----------|------|--------|
| `aggregate_model_scores` | `backend/services/model_scorer.py` | 主入口：聚合 → 评分 → EMA → 审核 → 写入知识库/日志 | - | None |
| `_query_aggregated_metrics` | `backend/services/model_scorer.py` | 聚合 7 天 knowledge_metrics 数据 | - | List[Dict] |
| `_compute_raw_score` | `backend/services/model_scorer.py` | 加权综合评分（成功率40%+延迟25%+重试15%+错误10%+基准10%） | row | float |
| `_apply_ema` | `backend/services/model_scorer.py` | EMA 平滑（α=0.2） | raw_score, old_score | float |
| `_get_confidence` | `backend/services/model_scorer.py` | 按样本量分级 confidence（<10→0.3, <50→0.7, ≥50→0.9） | sample_count | float |
| `_determine_status` | `backend/services/model_scorer.py` | 判断审核状态（Δ≥0.1 或样本<20 → pending_review） | ema_score, old_score, sample_count | str |
| `_get_latest_score` | `backend/services/model_scorer.py` | 查询最近一次已生效评分 | model_id, task_type | Optional[float] |
| `_write_score_to_knowledge` | `backend/services/model_scorer.py` | 写入评分知识节点（source=aggregated） | row, score, confidence | Optional[str] |
| `_write_audit_log` | `backend/services/model_scorer.py` | 写入 scoring_audit_log 审核记录 | row, old_score, new_score, status, node_id | None |
| `BackgroundTaskWorker._run_model_scoring` | `backend/services/background_task_worker.py` | 每小时触发模型评分聚合（节流） | - | None |

#### 配置常量

| 常量名 | 值 | 文件路径 | 说明 |
|--------|-----|----------|------|
| `EMA_ALPHA` | 0.2 | `backend/services/model_scorer.py` | EMA 新数据权重 |
| `AGGREGATION_WINDOW_DAYS` | 7 | `backend/services/model_scorer.py` | 聚合窗口天数 |
| `LATENCY_MAX_MS` | 30000 | `backend/services/model_scorer.py` | 延迟评分最差基准 |
| `REVIEW_SCORE_CHANGE_THRESHOLD` | 0.1 | `backend/services/model_scorer.py` | 触发人工审核的分数变化阈值 |
| `REVIEW_MIN_SAMPLE_COUNT` | 20 | `backend/services/model_scorer.py` | 触发人工审核的最小样本量 |

### 知识系统信号接入模块 (Knowledge Signal Pipeline)

> **新增于信号接入增强**：将路由决策、用户反馈、记忆检索、生成耗时等数据信号全链路接入 knowledge_metrics，供 EMA 评分聚合使用。

#### 后端函数

| 函数名 | 文件路径 | 功能描述 | 参数 | 返回值 |
|--------|----------|----------|------|--------|
| `MessageMixin._calc_task_elapsed_ms` | `backend/services/handlers/mixins/message_mixin.py` | 从 task.created_at 计算任务耗时（毫秒） | task | Optional[int] |
| `MessagePersistenceMixin._upsert_assistant_message` | `backend/services/handlers/mixins/message_persistence_mixin.py` | 合并 generation_params 并 upsert 助手消息 | message fields | tuple[Message, dict] |
| `MessagePersistenceMixin._check_idempotency` | `backend/services/handlers/mixins/message_persistence_mixin.py` | 检查终态任务并保护用户中断锚点不被覆盖 | task, task_id | Optional[Message] |
| `RetryKnowledgeMixin._send_retry_notification` | `backend/services/handlers/retry_knowledge_mixin.py` | 使用 Handler 当前 org_id 推送模型重试通知 | task_id, conversation_id, user_id, new_model, attempt | None |
| `IntentRouter._record_routing_signal` | `backend/services/intent_router.py` | 记录意图路由决策信号（fire-and-forget） | decision, user_id, input_length, has_image, router_model | None |
| `AgentLoop.run` | `backend/services/agent_loop.py` | 执行 Agent Loop，返回路由结果 | content, thinking_mode?, task_id? | AgentResult |
| `AgentLoop._record_loop_signal` | `backend/services/agent_loop.py` | 记录 Agent Loop 路由信号（含 loop_turns/tokens） | result, input_length, has_image | None |
| `_record_user_feedback_signal` | `backend/api/routes/message.py` | 记录用户反馈信号（retry/regenerate/regenerate_single） | db, user_id, operation, model, gen_type, original_message_id, conversation_id | None |

#### 信号类型（task_type 值）

| task_type | 来源 | 关键 params 字段 |
|-----------|------|-----------------|
| `image` / `video` | 成功/失败回调 | cost_time_ms, retried, retry_from_model |
| `routing` | IntentRouter / AgentLoop | routing_tool, routed_by, recommended_model, input_length, has_image |
| `user_feedback` | message.py 操作分发 | feedback_type, original_model, new_model, original_task_type |

---

### ERP API 搜索模块 (ERP API Search)

> **新增于快麦 ERP + 淘宝奇门接入**：提供两种查询模式（精确/关键词），支持按需发现 API 操作和参数文档。

#### 后端函数

| 函数名 | 文件路径 | 功能描述 | 参数 | 返回值 |
|--------|----------|----------|------|--------|
| `search_erp_api` | `backend/services/kuaimai/api_search.py` | 搜索 ERP 可用的 API 操作和参数文档 | query: str | str |
| `_exact_search` | `backend/services/kuaimai/api_search.py` | 精确查询：tool:action 格式 | query: str | str |
| `_keyword_search` | `backend/services/kuaimai/api_search.py` | 关键词搜索：在 action 名称和描述中模糊匹配 | query: str | str |
| `_calc_match_score` | `backend/services/kuaimai/api_search.py` | 计算关键词匹配分数（action+3, description+2, params+1） | keywords, tool_name, action_name, entry | int |
| `_format_entry_detail` | `backend/services/kuaimai/api_search.py` | 格式化单个 API 操作的完整文档（含参数、默认值、是否写操作） | tool_name, action_name, entry | str |
| `_format_entry_brief` | `backend/services/kuaimai/api_search.py` | 格式化 API 操作的简要信息摘要 | tool_name, action_name, entry | str |
| `_format_tool_actions` | `backend/services/kuaimai/api_search.py` | 列出工具的所有操作（摘要格式） | tool_name, registry | str |

---

### 工具注册表模块 (Tool Registry)

> **新增于工具系统统一架构**：统一工具元数据（tags/priority/domain）+ 同义词扩展表。

#### 后端函数

| 函数名 | 文件路径 | 功能描述 | 参数 | 返回值 |
|--------|----------|----------|------|--------|
| `register` | `backend/config/tool_registry.py` | 注册工具到全局表 | entry: ToolEntry | ToolEntry |
| `get_domain_tools` | `backend/config/tool_registry.py` | 获取指定 domain 的所有工具（含 always_include） | domain: str | List[ToolEntry] |
| `expand_synonyms` | `backend/config/tool_registry.py` | 同义词扩展（子串匹配，零依赖） | user_input: str | Set[str] |

---

### 工具智能筛选器 (Tool Selector)

> **新增于工具系统统一架构**：三级匹配（同义词+tags+qwen-turbo）+ action 筛选 + 兜底扩充。

#### 后端函数

| 函数名 | 文件路径 | 功能描述 | 参数 | 返回值 |
|--------|----------|----------|------|--------|
| `select_and_filter_tools` | `backend/services/tool_selector.py` | 主入口：三级匹配 + action 筛选，返回过滤后的 tool schemas | domain, user_input, all_tool_schemas | List[Dict] |
| `select_tools` | `backend/services/tool_selector.py` | Level 1+2 工具筛选（同义词+tags 子串匹配） | domain, user_input, top_k | (List[ToolEntry], Set[str]) |
| `_score_actions` | `backend/services/tool_selector.py` | 筛选工具内 action（子串匹配+权重） | tool_name, user_input, match_words | Optional[List[str]] |
| `_semantic_tool_match` | `backend/services/tool_selector.py` | Level 3: qwen-turbo 语义匹配（L1+L2 命中 < 3 时触发） | user_input, candidate_tools | List[str] |
| `_filter_tool_schema_actions` | `backend/services/tool_selector.py` | 深拷贝 schema 并过滤 action enum | schema, allowed_actions | Dict |

---

### 智能模型配置模块 (Smart Model Config)

> **增强部分**：新增模型能力标签生成、模型选择校验、对话配置查询等函数。

#### 后端函数（新增/修改）

| 函数名 | 文件路径 | 功能描述 | 参数 | 返回值 |
|--------|----------|----------|------|--------|
| `_build_capability_tags` | `backend/config/smart_model_config.py` | 从模型配置生成能力标签字符串 | model | str |
| `_find_model_config` | `backend/config/smart_model_config.py` | 在 chat 模型列表中查找指定模型配置 | model_id: str | Optional[Dict[str, Any]] |
| `_get_models_with_capability` | `backend/config/smart_model_config.py` | 获取具有指定能力的 chat 模型列表（按 priority 排序） | capability: str, value: bool | List[str] |
| `validate_model_choice` | `backend/config/smart_model_config.py` | 校验模型选择是否匹配需求，不匹配时返回警告文本 | model_id, has_image, needs_search | Optional[str] |
| `_get_model_desc` | `backend/config/smart_model_config.py` | **修改**：获取指定类别的模型描述文本，chat 类型自动附加能力标签 | category: str | str |

---

### ERP 工具定义模块 (ERP Tools)

> **增强部分**：新增 action 描述生成函数，支持丰富的参数文档。

#### 后端函数（新增）

| 函数名 | 文件路径 | 功能描述 | 参数 | 返回值 |
|--------|----------|----------|------|--------|
| `_format_action_desc` | `backend/config/erp_tools.py` | 生成单个 action 的丰富描述（name=描述(参数列表)） | name: str, entry | str |
| `build_erp_search_tool` | `backend/config/erp_tools.py` | 构建 ERP API 搜索工具定义（供千问 Function Calling 使用） | - | Dict[str, Any] |

---

### 统一查询引擎 (Unified Query Engine — Filter DSL)

> **新增于统一查询引擎重构**：替代 7 个碎片工具（purchase_query/aftersale_query/order_query/product_flow/doc_query/global_stats/db_export），统一对 erp_document_items 的查询入口。设计文档: `docs/document/TECH_统一查询引擎FilterDSL.md`

#### 后端函数

| 函数名 | 文件路径 | 功能描述 | 参数 | 返回值 |
|--------|----------|----------|------|--------|
| `UnifiedQueryEngine.execute` | `backend/services/kuaimai/erp_unified_query.py` | 统一查询入口（summary/detail/export 三种模式） | doc_type, mode, filters, group_by, sort_by, fields, limit, time_type, ... | str |
| `UnifiedQueryEngine._summary` | `backend/services/kuaimai/erp_unified_query.py` | 调 RPC 返回聚合统计 | doc_type, filters, time_range, group_by, request_ctx | str |
| `UnifiedQueryEngine._detail` | `backend/services/kuaimai/erp_unified_query.py` | ORM 查询返回明细行（热表+冷表 UNION） | doc_type, filters, time_range, fields, sort_by, sort_dir, limit, request_ctx | str |
| `UnifiedQueryEngine._export` | `backend/services/kuaimai/erp_unified_query.py` | ORM 批量查询 + Parquet 写入 staging | doc_type, filters, time_range, fields, limit, user_id, conversation_id, request_ctx | str |
| `_validate_filters` | `backend/services/kuaimai/erp_unified_query.py` | 校验 Filter DSL 合法性（白名单+类型兼容） | filters: list[dict] | (list[ValidatedFilter], error_msg) |
| `_extract_time_range` | `backend/services/kuaimai/erp_unified_query.py` | 从 filters 提取时间范围（按 mode 默认） | filters, time_type, request_ctx, mode | TimeRange |
| `_apply_orm_filters` | `backend/services/kuaimai/erp_unified_query.py` | ValidatedFilter → Supabase ORM 链式调用 | q, filters | q |

#### Schema 常量

| 常量名 | 文件路径 | 功能描述 |
|--------|----------|----------|
| `COLUMN_WHITELIST` | `backend/services/kuaimai/erp_unified_schema.py` | 列白名单（35列，含类型元数据） |
| `OP_COMPAT` | `backend/services/kuaimai/erp_unified_schema.py` | op 与列类型兼容表 |
| `DEFAULT_DETAIL_FIELDS` | `backend/services/kuaimai/erp_unified_schema.py` | detail 模式各 doc_type 默认返回字段 |
| `EXPORT_COLUMNS` | `backend/services/kuaimai/erp_unified_schema.py` | export 模式可导出字段文档 |

---

### Excel 三层清洗模块 (Excel Cleaner)

> **新增**：2026-05-07。三层清洗防线（结构检测 / 智能清洗 / 质量校验），独立于 data_query_cache.py。

#### 后端函数

| 函数名 | 文件路径 | 功能描述 | 参数 | 返回值 |
|--------|----------|----------|------|--------|
| `clean_excel` | `backend/services/agent/excel_cleaner.py` | 三层清洗入口 | df, excel_path, sheet_name, header_row | `(DataFrame, CleaningReport)` |
| `write_cleaning_report` | `backend/services/agent/excel_cleaner.py` | 将清洗报告写入 .meta.json | cache_path, report | None |
| `read_cleaning_report` | `backend/services/agent/excel_cleaner.py` | 读取 .meta.json 清洗报告 | cache_path | `CleaningReport \| None` |

#### 数据结构

| 类名 | 文件路径 | 说明 |
|------|----------|------|
| `ExcelStructure` | `backend/services/agent/excel_cleaner.py` | Layer 1 结构检测结果（合并区域/隐藏行列/筛选） |
| `CleaningReport` | `backend/services/agent/excel_cleaner.py` | 清洗报告（各项清洗计数 + warnings + LLM 文本生成） |

---

### 快麦参数映射模块 (Kuaimai Param Mapper)

> **修改部分**：map_params 返回类型变更。

#### 后端函数（修改）

| 函数名 | 文件路径 | 功能描述 | 参数 | 返回值 |
|--------|----------|----------|------|--------|
| `map_params` | `backend/services/kuaimai/param_mapper.py` | **修改**：将用户参数映射为 API 参数（带白名单校验） | entry, user_params | Tuple[Dict[str, Any], List[str]] |

### 后端服务辅助模块 (Backend Service Helpers)

#### 后端函数

| 函数名 | 文件路径 | 功能描述 | 参数 | 返回值 |
|--------|----------|----------|------|--------|
| `format_message` | `backend/services/message_utils.py` | 格式化消息响应，并透传可选 Turn/reply/revision 字段 | message_data | dict |
| `deduct_user_credits` | `backend/services/message_utils.py` | 扣除用户积分 | db, user_id, credits, description | None |
| `_generate_with_credits` | `backend/services/video_service.py` | 通用视频生成流程 | user_id, model, ... | Dict[str, Any] |
| `_get_user` | `backend/services/base_generation_service.py` | 获取用户信息 | user_id | dict |
| `_check_credits` | `backend/services/base_generation_service.py` | 检查积分是否足够 | user, required_credits | None |
| `_deduct_credits` | `backend/services/base_generation_service.py` | 扣除积分 | user_id, credits, description | int |
| `record_user_activity` | `backend/services/user_activity_service.py` | 记录用户活跃事件并更新 users.last_active_at（失败不阻断） | db, user_id, event_type, org_id?, source?, resource_type?, resource_id?, occurred_at?, metadata? | None |

### 企业微信入站余额校验

#### 后端函数 — WecomAIMixin (`backend/services/wecom/wecom_ai_mixin.py`)

| 函数名 | 文件路径 | 功能描述 | 参数 | 返回值 |
|--------|----------|----------|------|--------|
| `_get_user_balance` | `backend/services/wecom/wecom_ai_mixin.py` | 获取用户积分余额 | user_id | int |

企微生成、上下文构建、积分扣除和结果投递已统一由 Conversation Actor、
ChatGenerationExecutor 与持久 Outbox 负责，不再由该 Mixin 建立第二条同步链路。

#### 后端函数 — 企微消息发送 (`backend/services/wecom/app_message_sender.py`)

| 函数名 | 文件路径 | 功能描述 | 参数 | 返回值 |
|--------|----------|----------|------|--------|
| `send_image` | `backend/services/wecom/app_message_sender.py` | 发送图片消息给企微用户 | wecom_userid, media_id, agent_id | bool |
| `send_video` | `backend/services/wecom/app_message_sender.py` | 发送视频消息给企微用户 | wecom_userid, media_id, title, description, agent_id | bool |
| `upload_temp_media` | `backend/services/wecom/app_message_sender.py` | 下载文件并上传到企微临时素材库 | file_url, media_type | Optional[str] |

### 企微 OAuth 认证模块 (WeChat Work OAuth)

#### 后端函数 — WecomOAuthService (`backend/services/wecom_oauth_service.py`)

| 函数名 | 文件路径 | 功能描述 | 参数 | 返回值 |
|--------|----------|----------|------|--------|
| `WecomOAuthService.generate_state` | `backend/services/wecom_oauth_service.py` | 生成 OAuth state token | state_type, user_id? | str |
| `WecomOAuthService.validate_state` | `backend/services/wecom_oauth_service.py` | 校验并消费 state（Redis GETDEL） | state | dict |
| `WecomOAuthService.exchange_code` | `backend/services/wecom_oauth_service.py` | 用授权 code 换取企微 userid | code | str |
| `WecomOAuthService.login_or_create` | `backend/services/wecom_oauth_service.py` | 企微用户登录或自动创建账号 | wecom_userid, nickname? | User |
| `WecomOAuthService.bind_account` | `backend/services/wecom_oauth_service.py` | 绑定企微账号到已有用户 | user_id, wecom_userid, nickname? | None |
| `WecomOAuthService.unbind_account` | `backend/services/wecom_oauth_service.py` | 解绑企微账号 | user_id | None |
| `WecomOAuthService.get_binding_status` | `backend/services/wecom_oauth_service.py` | 查询用户企微绑定状态 | user_id | dict |
| `WecomOAuthService.build_qr_url` | `backend/services/wecom_oauth_service.py` | 构建企微扫码登录 URL | state | str |

#### 后端函数 — 账号合并 (`backend/services/wecom_account_merge.py`)

| 函数名 | 文件路径 | 功能描述 | 参数 | 返回值 |
|--------|----------|----------|------|--------|
| `merge_users` | `backend/services/wecom_account_merge.py` | 合并两个用户的数据（对话/消息/积分等） | db, keep_user_id, remove_user_id, ... | None |

#### 后端函数 — 企微 OAuth 路由 (`backend/api/routes/wecom_auth.py`)

| 路由 | 文件路径 | 功能描述 | 方法 | 返回值 |
|------|----------|----------|------|--------|
| `/api/auth/wecom/qr-url` | `backend/api/routes/wecom_auth.py` | 获取企微扫码登录 URL | GET | `{url, state}` |
| `/api/auth/wecom/callback` | `backend/api/routes/wecom_auth.py` | OAuth 授权回调处理 | GET | 重定向/Token |
| `/api/auth/wecom/binding` | `backend/api/routes/wecom_auth.py` | 解绑企微账号 | DELETE | `{success}` |
| `/api/auth/wecom/binding-status` | `backend/api/routes/wecom_auth.py` | 查询企微绑定状态 | GET | `{bound, wecom_userid}` |

#### 前端组件

| 组件名 | 文件路径 | 功能描述 | Props | 说明 |
|--------|----------|----------|-------|------|
| `WecomQrLogin` | `frontend/src/components/auth/WecomQrLogin.tsx` | 企微二维码登录组件 | - | 展示企微扫码二维码，轮询登录状态 |
| `WecomCallback` | `frontend/src/pages/WecomCallback.tsx` | OAuth 回调着陆页 | - | 处理企微 OAuth 回调，完成登录/绑定流程 |

---

## 函数分类索引

### 按模块分类
- **Redis 基础设施模块**：6个后端函数
- **任务限制服务模块**：4个后端函数
- **任务管理模块**：12个后端函数 + 8个前端函数（含 BackgroundTaskWorker 轮询兜底）
- **积分管理模块**：7个后端函数
- **对话管理模块**：5个后端函数 + 5个前端函数
- **消息处理模块**：7个前端函数（统一 useMediaMessageHandler）
- **滚动管理模块**：1个前端函数（useVirtuaScroll 统一入口，使用 Virtua 替代 Virtuoso）
- **重新生成模块**：3个前端函数
- **任务恢复模块**：1个前端函数（WebSocket 实时推送替代轮询）
- **统一消息发送模块**：5个前端函数（统一 sendMessage）
- **媒体重新生成模块**：6个前端函数
- **Webhook 回调与任务完成服务模块**：19个后端函数（✨Webhook 改造新增）
- **任务通知模块**：1个前端函数 + 5个类型定义（✨阶段4新增）
- **图片URL工具模块**：2个前端函数（✨阶段0新增）
- **统一日志工具模块**：4个前端函数（✨阶段0新增）
- **任务协调器模块**：4个前端函数
- **消息合并工具模块**：1个前端函数
- **记忆模块**：10个后端函数 + 3个前端函数 + 7个配置常量（✨记忆智能过滤）
- **模型动态评分模块**：10个后端函数 + 5个配置常量（✨Agent 知识库动态评分）
- **ERP API 搜索模块**：7个后端函数（✨快麦ERP接入）
- **AI 模型搜索模块**：6个后端函数（✨智能模型配置增强）
- **智能模型配置模块**：5个后端函数（✨模型能力标签和校验）
- **ERP 工具定义模块**：2个后端函数（✨ERP工具增强）
- **快麦参数映射模块**：1个后端函数修改（✨参数映射增强）
- **性能监控模块**：9个前端函数
- **企业微信 AI 路由模块**：11个后端函数（WecomAIMixin）+ 3个后端函数（app_message_sender）（✨企微Agent Loop对接）
- **企微 OAuth 认证模块**：8个后端函数（WecomOAuthService）+ 1个后端函数（账号合并）+ 4个路由 + 2个前端组件（✨企微扫码登录与账号绑定）
- **测试工具模块**：4个前端函数
- **消息服务模块**：8个后端函数 + 5个前端函数
- **图像生成模块**：3个后端函数 + 5个前端函数
- **视频生成模块**：5个后端函数 + 6个前端函数
- **用户设置模块**：3个前端函数
- **KIE 适配器模块**：6个后端函数
- **预定义常量**：13个性能标记常量 + 3个媒体默认值常量
- **总计**：约 270+ 个函数/类型

### 按功能分类
- **Redis 操作**：`RedisClient.get_client`, `RedisClient.acquire_lock`, `RedisClient.release_lock`
- **任务限制**：`check_and_acquire`, `release`, `get_active_count`, `can_start_task`
- **任务创建与提交**：`create_task`, `submitTask`, `checkTaskLimits`
- **任务状态管理**：`update_task_status`, `handle_task_completion`, `handle_task_failure`, `useMessageStore`, `mergeTasks`
- **任务完成处理（Webhook/轮询）**：`handle_webhook`, `TaskCompletionService.process_result`, `parse_callback`, `extract_task_id`
- **任务查询**：`get_active_tasks`, `count_active_tasks`, `count_conversation_active_tasks`, `getConversationTaskBadge`
- **轮询兜底**：`BackgroundTaskWorker.poll_pending_tasks`, `query_and_process`, `cleanup_stale_tasks`
- **AI调用**：`call_ai_api`, `process_task_worker`
- **实时通信**：`subscribeTaskUpdates`, `handleTaskProgress`, `handleTaskCompleted`
- **积分操作**：`lock_credits`, `confirm_deduct`, `refund_credits`, `credit_lock`, `deduct_atomic`, `get_balance`
- **对话管理**：`create_conversation`, `update_conversation_title`, `get_conversation_list`, `delete_conversation`
- **标题管理**：`generate_auto_title`, `generateAutoTitle`, `updateConversationTitle`, `syncTitleToNavbar`, `handleTitleEdit`
- **企微 OAuth 认证**：`WecomOAuthService.generate_state`, `validate_state`, `exchange_code`, `login_or_create`, `bind_account`, `unbind_account`, `build_qr_url`, `merge_users`

---

## 统计信息
- **总函数数**：约 285+ 个（规划中 + 已实现）
- **已实现组件**：37 个（30 聊天组件 + 6 认证组件 + 1 通用组件）
- **已实现 Hooks**：50+ 个自定义 Hooks（含消息处理、滚动管理、重新生成等）
- **已实现模块**：Redis 基础设施、任务限制服务、积分服务、消息处理、消息服务、滚动管理、重新生成、轮询管理、**统一消息发送**（含 mediaSender）、媒体重新生成、**任务通知**、**图片URL工具**、**统一日志**、**任务协调器**、**消息合并**、性能监控、图像生成、视频生成、用户设置、KIE 适配器、聊天模块、任务状态管理、测试工具、认证弹窗模块、通用组件模块、占位符管理模块、**Webhook 回调与任务完成服务**、**批次完成处理服务**、**ERP API 搜索**、**AI 模型搜索**、**智能模型配置**、**ERP 工具定义**、**企微 OAuth 认证**
- **测试覆盖率目标**：80%+（Vitest + Testing Library）
- **性能监控**：13个预定义性能标记，支持关键路径监控
- **最后更新**：2026-03-22（企微扫码登录与账号绑定）

---

## 相关文档

### 测试相关
- [测试指南](../frontend/TESTING.md) - Vitest 测试框架使用指南，包含单元测试、集成测试最佳实践
- [测试工具函数](../frontend/src/test/testUtils.tsx) - 自定义测试工具函数和 Mock 数据

### 性能监控
- [性能监控指南](../frontend/src/utils/PERFORMANCE_MONITORING.md) - PerformanceMonitor 使用指南，关键路径监控和优化建议
- [性能监控工具](../frontend/src/utils/performanceMonitor.ts) - 性能监控实现代码

### 消息处理
- [Handler 使用指南](../frontend/src/hooks/handlers/README.md) - 消息处理器（文本/图片/视频）完整使用文档和示例代码
- [消息处理器工具函数](../frontend/src/hooks/handlers/mediaHandlerUtils.ts) - 媒体处理工具函数实现

### 前端图片资产协议

| 函数 | 文件路径 | 功能描述 |
|------|---------|---------|
| `isThumbnailImageUrl` | `frontend/src/utils/imageUrlRules.ts` | 判断 URL 是否为独立缩略图对象，当前用于识别 `/workspace-thumbnails/` |
| `toOriginalImageUrl` | `frontend/src/utils/imageUrlRules.ts` | 原图规则入口；预览、下载、传模型前移除 OSS `x-oss-process` 参数，并拒绝独立缩略图对象 |
| `pickOriginalImageUrl` | `frontend/src/utils/imageUrlRules.ts` | 按候选字段顺序选择第一个合法原图 URL，避免旧数据中缩略图字段短路原图字段 |
| `toDisplayThumbnailUrl` | `frontend/src/utils/imageUrlRules.ts` | 缩略图展示入口；小图、缩略条、列表网格可使用独立缩略图，缺省时回退原图 |
| `toThumbnailImageUrl` | `frontend/src/utils/imageUrlRules.ts` | 旧缩略图兜底入口；委托给 `toDisplayThumbnailUrl`，不再生成 OSS 处理参数 |
| `resolveImageOriginalUrl` | `frontend/src/utils/messageUtils.ts` | 从图片 content part 解析原图 URL，逐个跳过缩略图候选，防止进入预览/下载链路 |
| `getImageAssets` | `frontend/src/utils/messageUtils.ts` | 从消息 content 提取图片资产对象，保留 `originalUrl` / `thumbnailUrl` 语义 |
| `fromImageAsset` | `frontend/src/preview/toPreviewItem.ts` | 将图片资产转换为 PreviewItem，主体预览/下载用原图，缩略条用缩略图 |
| `useImageUpload.addQuotedImage` | `frontend/src/hooks/useImageUpload.ts` | 引用图片加入输入框；显示可用缩略图，发送与入库保留原图 URL |
| `createAttachmentSubmissionSnapshot` | `frontend/src/components/chat/attachments/attachmentSubmission.ts` | 所有聊天附件的模型提交边界；图片只输出合法原图 URL，缩略图仅作为显示元数据 |

### 架构文档
- [项目概览](./PROJECT_OVERVIEW.md) - 项目整体架构和目录结构
- [当前问题](./CURRENT_ISSUES.md) - 待修复问题和开发进度追踪

---

## 多Agent架构模块（2026-04-16 新增）

> 设计文档: `docs/document/TECH_多Agent单一职责重构.md`

### 结构化数据协议

| 类名 | 文件路径 | 功能描述 |
|------|---------|---------|
| `ToolOutput` | `backend/services/agent/tool_output.py` | 统一工具输出（summary+data/file_ref+metadata） |
| `ColumnMeta` | `backend/services/agent/tool_output.py` | 列元信息（name+dtype+label） |
| `FileRef` | `backend/services/agent/tool_output.py` | Staging 文件引用（path+row_count+columns） |
| `SessionFileRegistry` | `backend/services/agent/session_file_registry.py` | 会话级文件注册表（冻结恢复支持） |

### 部门Agent

| 类名 | 文件路径 | 功能描述 |
|------|---------|---------|
| `DepartmentAgent` | `backend/services/agent/department_agent.py` | 基类：_build_output(FIELD_MAP) / _extract_field_from_context / validate / execute |
| `ValidationResult` | `backend/services/agent/department_types.py` | 参数校验三态（ok/missing/conflict） |
| `WarehouseAgent` | `backend/services/agent/departments/warehouse_agent.py` | 仓储域（库存/仓库/出入库） |
| `PurchaseAgent` | `backend/services/agent/departments/purchase_agent.py` | 采购域（采购单/供应商/采退） |
| `TradeAgent` | `backend/services/agent/departments/trade_agent.py` | 订单域（订单/物流/发货） |
| `AftersaleAgent` | `backend/services/agent/departments/aftersale_agent.py` | 售后域（退货/退款/售后） |

### 计算Agent

| 类名 | 文件路径 | 功能描述 |
|------|---------|---------|
| `ComputeAgent` | `backend/services/agent/compute_agent.py` | 独立计算Agent（prompt构建+输入格式化） |
| `ComputeTask` | `backend/services/agent/compute_types.py` | 计算任务输入（instruction+inputs+output_format） |
| `ComputeResult` | `backend/services/agent/compute_types.py` | 计算任务输出（conclusion+output+warnings） |
| `validate_compute_result` | `backend/services/agent/compute_types.py` | 结果硬校验纯函数 |

### DAG 编排引擎

| 类名 | 文件路径 | 功能描述 |
|------|---------|---------|
| `ExecutionPlan` | `backend/services/agent/execution_plan.py` | DAG 执行计划（rounds+validate+abort） |
| `Round` | `backend/services/agent/execution_plan.py` | DAG 单轮（agents+task+depends_on） |
| `PlanBuilder` | `backend/services/agent/plan_builder.py` | 三级降级链（LLM→关键词→abort） |
| `DAGExecutor` | `backend/services/agent/dag_executor.py` | Round 编排引擎（并行+错误传播+PARTIAL阈值） |
| `ExperienceRecorder` | `backend/services/agent/experience_recorder.py` | Agent经验记录（路由/失败→知识库） |

### 沙盒代码执行（Sandbox）

| 类/函数名 | 文件路径 | 功能描述 |
|-----------|---------|---------|
| `SandboxExecutor` | `backend/services/sandbox/executor.py` | 沙盒执行器（AST验证+文件快照+有状态/无状态执行+文件上传） |
| `SandboxExecutor.execute` | `backend/services/sandbox/executor.py` | 执行代码（优先有状态Kernel，fallback无状态subprocess） |
| `SandboxExecutor._backup_workspace_files` | `backend/services/sandbox/executor.py` | 执行前备份workspace数据文件到STAGING_DIR（_bak_{ts}_{name}格式） |
| `SandboxExecutor._cleanup_workspace_backups` | `backend/services/sandbox/executor.py` | 执行后清理未修改的备份，返回{文件名:备份路径}供registry注册 |
| `KernelManager` | `backend/services/sandbox/kernel_manager.py` | Kernel进程池管理器（创建/复用/回收/降级） |
| `KernelManager.get_or_create` | `backend/services/sandbox/kernel_manager.py` | 获取或创建Kernel（超限降级返回False） |
| `KernelManager.execute` | `backend/services/sandbox/kernel_manager.py` | 向Kernel发送代码并等待结果 |
| `kernel_main` | `backend/services/sandbox/kernel_worker.py` | Kernel Worker REPL主循环（stdin/stdout JSON-Line） |
| `build_sandbox_executor` | `backend/services/sandbox/functions.py` | 工厂函数（构建执行器+注入KernelManager） |
| `validate_code` | `backend/services/sandbox/validators.py` | AST安全预检（模块/函数黑名单+dunder限制） |
| `get_kernel_manager` | `backend/services/sandbox/kernel_manager.py` | 获取全局KernelManager单例 |

### 文件操作模块（FileExecutor 三 Mixin 架构）

| 类/函数名 | 文件路径 | 功能描述 |
|-----------|---------|---------|
| `FileExecutor` | `backend/services/file_executor.py` | 文件操作执行器（路径安全校验 + Query/Write Mixin 组合） |
| `FileOperationError` | `backend/services/file_executor.py` | 文件操作业务校验异常（参数/路径问题，LLM 可重试） |
| `FileReadResult` | `backend/schemas/multimodal.py` | 多模态工具返回类型（type=image 触发 chat_handler 注入 image_url 多模态块） |
| `FileQueryExtensionsMixin` | `backend/services/file_query_extensions.py` | 文件查询+编辑 Mixin |
| `file_list_entries` | `backend/services/file_query_extensions.py` | 列目录（结构化数据返回） |
| `file_list` | `backend/services/file_query_extensions.py` | 列目录（格式化文本） |
| `file_search` | `backend/services/file_query_extensions.py` | 搜索文件（文件名/内容） |
| `file_info` | `backend/services/file_query_extensions.py` | 文件/目录元信息 |
| `file_edit` | `backend/services/file_query_extensions.py` | 精确字符串替换（对标 Claude Code Edit） |
| `FileWriteExtensionsMixin` | `backend/services/file_write_extensions.py` | 文件写入+管理 Mixin |
| `file_write` | `backend/services/file_write_extensions.py` | 写入文件（覆盖/追加/仅创建） |
| `file_delete` | `backend/services/file_write_extensions.py` | 删除文件或空目录 |
| `file_mkdir` | `backend/services/file_write_extensions.py` | 创建目录（含中间路径） |
| `file_rename` | `backend/services/file_write_extensions.py` | 重命名（同目录） |
| `file_move` | `backend/services/file_write_extensions.py` | 移动文件到目标目录 |
| `_restore_file` | `backend/services/agent/file_tool_mixin.py` | 从registry查找备份并恢复workspace文件（restore_file工具执行逻辑） |
| `analyze_file` | `backend/services/agent/file_analysis_service.py` | 编排 file_analyze 的安全路径解析、Parquet 转换、超时错误、真实 Parquet 访问契约与缓存登记 |
| `_sandbox_parquet_path` | `backend/services/agent/file_analysis_service.py` | 校验真实缓存文件位于当前 staging，并投影为 code_execute 可直接读取的相对路径 |
| `_sandbox_original_path` | `backend/services/agent/file_analysis_service.py` | 将原始文件路径转换为沙盒相对路径，外部资源仅暴露文件名 |
| `ChatToolResultMixin` | `backend/services/handlers/chat_tool_result_mixin.py` | 统一分类、推送、审计单次工具执行结果 |
| `_register_workspace_backups` | `backend/services/agent/tool_executor.py` | 将workspace备份注册到对话级session_file_registry |

### 文件 ID 协议（file_id）

> 解决 LLM 在生成 tool_call 时把中文 path 自动 "pangu 化"（中英文间加空格）导致文件读不到的问题。AI 看到短 ASCII 的 `fid_xxx` 而非中文路径。详见 `docs/document/TECH_文件ID协议化.md`。

| 函数/常量 | 文件路径 | 功能描述 |
|-----------|---------|---------|
| `compute_fid` | `backend/services/agent/file_id.py` | 确定性哈希 `(org_id, workspace_path) → fid_xxx`（blake2b 4字节 → 12 位 ASCII） |
| `is_valid_fid` | `backend/services/agent/file_id.py` | 校验 fid 格式 `^fid_[a-z0-9]{8}$` |
| `resolve_fid_to_workspace` | `backend/services/agent/file_id.py` | 从 file_path_cache 反查 fid 对应的 workspace 绝对路径 |
| `format_attachments` | `backend/services/handlers/chat_context/attachments.py` | XML 渲染附件，每个 `<file>` 含 `<id>fid_xxx</id>` + `<name>` + `<path>` + 附件使用规则 |
| `build_workspace_prompt` | `backend/services/handlers/chat_context/attachments.py` | 工作区清单，每行带 `[fid_xxx]` 前缀 |

### 企业微信会话与附件入口

| 函数/对象 | 文件路径 | 功能描述 |
|-----------|---------|---------|
| `normalize_wecom_message` | `backend/services/wecom/message_normalizer.py` | 将智能机器人回调规范化为稳定的企微消息协议 |
| `resolve_wecom_conversation` | `backend/services/wecom/channel_conversation.py` | 按私聊用户或群聊 channel binding 解析会话 |
| `stage_wecom_attachment` | `backend/services/wecom/attachment_service.py` | 按企微 msgid 幂等暂存 FILE 资产，不启动生成任务 |
| `consume_active_conversation_attachments` | `backend/migrations/130_wecom_actor_attachment_consumption.sql` | 在会话事务锁内消费活动附件并生成冻结 FilePart |
| `enqueue_wecom_generation_turn` | `backend/migrations/130_wecom_actor_attachment_consumption.sql` | 原子创建企微输入/输出消息、消费附件并进入 Actor 队列 |
| `resolve_execution_scope` | `backend/services/handlers/chat/execution_scope.py` | 从数据库会话和企微 delivery context 解析可信 user/channel 执行作用域 |
| `build_wecom_channel_workspace_owner` | `backend/core/workspace.py` | 生成稳定且不可由客户端指定的企微群 Workspace owner |
| `partition_tool_calls` / `resolve_file_ids` | `backend/services/handlers/chat_tool_helpers.py` | Chat 工具分批与文件 ID 安全路径转换 |
| `ConversationToolMixin` | `backend/services/agent/conversation_tool_mixin.py` | ToolExecutor 的当前会话历史读取职责 |
| `FileDescribeMixin` | `backend/services/agent/file_describe_mixin.py` | 文件搜索命中单文件后的描述和图片多模态返回 |
| `ERPChildFactoryMixin` | `backend/services/agent/erp_child_factory_mixin.py` | 按执行 Workspace scope 创建 ERP 部门子 Agent |

### 工作区分类与批量下载

> 工作区文件面板 Tab 分类筛选（全部/文档/图片与视频）+ 图片/视频双击预览 + 批量下载 ZIP。详见 `docs/document/TECH_工作区分类与批量下载.md`。

#### 后端函数

| 函数 | 文件路径 | 功能描述 |
|------|---------|---------|
| `download_workspace_zip` | `backend/api/routes/file_download.py` | POST endpoint：流式 ZIP 打包多文件/文件夹（zipstream-ng，UTF-8 中文文件名，500 文件/2GB 上限） |
| `_collect_zip_targets` | `backend/api/routes/file_download.py` | 解析 + 校验 + 递归展开路径列表为 (绝对路径, arcname) 元组列表（自动排除 `.` 开头隐藏文件，与 listdir/search 对齐） |
| `_resolve_archive_name` | `backend/api/routes/file_download.py` | 决定 ZIP 文件名（单文件夹→folder.zip / 多个→workspace-{ts}.zip） |
| `get_executor` | `backend/api/routes/file_common.py` | workspace 路由共用的 FileExecutor 工厂（拆分时提取自原 `_get_executor`） |

#### 前端函数

| 函数/组件 | 文件路径 | 功能描述 |
|----------|---------|---------|
| `categorize` | `frontend/src/utils/fileCategory.ts` | 判定文件分类（image/video/document，扩展名优先 + mime 兜底） |
| `matchesFilter` | `frontend/src/utils/fileCategory.ts` | 判断文件是否属于当前 Tab 筛选 |
| `canPreviewImage` / `canPreviewVideo` | `frontend/src/utils/fileCategory.ts` | 双击是否应弹图片/视频预览 |
| `downloadWorkspaceZip` | `frontend/src/services/workspace.ts` | POST ZIP 接口 + blob 接收 + 触发浏览器下载（含 RFC 5987 文件名解析） |
| `WorkspaceCategoryTabs` | `frontend/src/components/workspace/WorkspaceCategoryTabs.tsx` | 分类 Tab 组件（蓝色下划线选中态） |
| `VideoPreviewModal` | `frontend/src/components/chat/media/VideoPreviewModal.tsx` | 视频全屏预览 Modal（Portal + `<video controls>` + ESC + ←→ 切换） |
| `useWorkspace.categoryFilter` | `frontend/src/hooks/useWorkspace.ts` | 当前 Tab 筛选状态（不持久化，切目录重置 all） |

### AI 媒体产物落盘

> 把 KIE/CDN 生成的图片/视频下载到用户工作区「下载/AI图片(AI视频)」目录,产出双轨 emit_payload(CDN URL + workspace_path),让工作区面板可见、可批量下载。失败时降级保留原 CDN URL,聊天里仍能看图,不退积分。

#### 后端函数

| 函数 | 文件路径 | 功能描述 |
|------|---------|---------|
| `download_url_to_workspace` | `backend/services/file_upload.py` | 单 URL → 工作区落盘 + 双轨 payload。复用 HttpDownloader + tenacity(3次/总45s预算) + upload_to_payload。命名 `IMG_<YYYYMMDD>_<HHMMSS>_<6hex>_<3idx>.<ext>`,MIME 白名单,写盘走 asyncio.to_thread,可选 .meta.json sidecar |
| `persist_media_urls_to_workspace` | `backend/services/file_upload.py` | 多 URL 并发落盘 helper(semaphore=5)。顺序保持,失败降级,extra_fields 透传 width/height/alt。media_tool_executor 与 image_agent 共用入口 |
| `build_workspace_thumbnail_url` | `backend/services/file_upload.py` | 根据 workspace 原图 URL 计算 `workspace-thumbnails` 独立缩略图 URL，不使用 OSS query 处理 |
| `_download_with_retry` | `backend/services/file_upload.py` | tenacity 装饰的下载封装:retry_if HTTPError/Timeout,3 次或 45s 总预算 stop |
| `_write_meta_sidecar` | `backend/services/file_upload.py` | 写隐藏 .meta.json sidecar(async to_thread,OSError 仅 warning) |
| `_generate_media_filename` | `backend/services/file_upload.py` | 生成行业标准命名(IMG/VID 前缀 + datetime + 短 hash + 序号 + 扩展名) |
| `_compute_user_root` | `backend/services/file_upload.py` | 计算用户工作区根目录(与 FileExecutor.__init__ 同步,org/personal 双模式) |

### 历史图片 URL 回填脚本

| 函数 | 文件路径 | 功能描述 |
|------|---------|---------|
| `load_env` | `backend/scripts/backfill_media_asset_urls.py` | 加载当前目录/backend/项目根 `.env`，用于本地和生产临时执行 |
| `strip_oss_process` | `backend/scripts/backfill_media_asset_urls.py` | 移除 URL 中的 `x-oss-process` 缩略参数，保留其他 query 参数 |
| `is_image_payload` | `backend/scripts/backfill_media_asset_urls.py` | 判断 JSON dict 是否为图片 payload |
| `iter_image_payloads` | `backend/scripts/backfill_media_asset_urls.py` | 递归遍历 JSON 中的图片 payload |
| `backfill_value` | `backend/scripts/backfill_media_asset_urls.py` | 给历史图片 payload 补 `original_url` 和独立 `workspace-thumbnails` 缩略图 URL |
| `fetch_rows` | `backend/scripts/backfill_media_asset_urls.py` | 按表/列读取包含图片 JSON 的候选行 |
| `process_column` | `backend/scripts/backfill_media_asset_urls.py` | dry-run/apply 单个 JSON 列的回填逻辑 |
| `build_thumbnail_resolver` | `backend/scripts/backfill_media_asset_urls.py` | 生产回填时从 NAS 原图生成并上传独立缩略图对象 |
| `merge_stats` | `backend/scripts/backfill_media_asset_urls.py` | 合并回填统计 |
| `main` | `backend/scripts/backfill_media_asset_urls.py` | CLI 入口，支持 `--dry-run` / `--apply` / `--limit` / `--sync-thumbnails` |

### 统一会话上下文历史回填

| 函数 | 文件路径 | 功能描述 |
|------|---------|---------|
| `canonical_json` / `content_hash` | `backend/scripts/backfill_conversation_context_items.py` | 生成跨进程稳定的 JSON 与 SHA-256 内容身份 |
| `stable_uuid` | `backend/scripts/backfill_conversation_context_items.py` | 按历史消息、块和 Artifact 身份生成可重跑 UUID |
| `decode_content` | `backend/scripts/backfill_conversation_context_items.py` | 将历史 `messages.content` 统一为结构化 block 列表 |
| `project_message` | `backend/scripts/backfill_conversation_context_items.py` | 将单条历史消息投影为 typed ConversationItem 和 `message_slice` Artifact |
| `iter_rows` | `backend/scripts/backfill_conversation_context_items.py` | 使用可跨批次提交的 server-side cursor 流式读取历史消息 |
| `insert_projection` | `backend/scripts/backfill_conversation_context_items.py` | 依靠稳定 ID 和唯一约束幂等写入一个历史投影 |
| `load_env` | `backend/scripts/backfill_conversation_context_items.py` | 加载项目数据库环境变量且不覆盖现有进程环境 |
| `main` | `backend/scripts/backfill_conversation_context_items.py` | 默认 dry-run，支持 apply、批大小和扫描上限的回填 CLI |
| `VerificationResult` | `backend/scripts/verify_conversation_context_backfill.py` | 汇总历史消息覆盖和统一上下文结构不变量，任一违规即关闭硬切换门禁 |
| `verify_backfill` | `backend/scripts/verify_conversation_context_backfill.py` | 在只读事务中核对消息覆盖、工具原子组、序列/revision 与 Artifact 引用完整性 |
| `main` | `backend/scripts/verify_conversation_context_backfill.py` | 执行全库或单会话只读验收，以 JSON 输出结果并用非零退出码阻断切换 |

### 通用工具 Artifact Runtime

| 函数/类 | 文件路径 | 功能描述 |
|------|---------|---------|
| `normalize_tool_result` | `backend/services/agent/runtime/artifacts/normalizer.py` | 将任意工具返回规范化为带稳定 ID、完整正文、哈希和双层模型视图的 ArtifactDraft |
| `canonical_json` | `backend/services/agent/runtime/artifacts/normalizer.py` | 规范化 dataclass、Decimal、日期、UUID 和枚举后生成稳定 JSON |
| `ArtifactStore` | `backend/services/agent/runtime/artifacts/store.py` | Run 内幂等登记、目录检索和 UTF-8 字节游标分页读取完整工具事实 |
| `page_content` | `backend/services/agent/runtime/artifacts/store.py` | 为 Run 内与持久 Artifact 提供统一 UTF-8 游标分页协议 |
| `PersistentArtifactRepository` | `backend/services/agent/runtime/artifacts/repository.py` | 按 conversation、base revision 和 org 隔离检索/读取 inline、OSS 与 message_slice 历史 Artifact |
| `project_tool_result` | `backend/services/agent/runtime/artifacts/projector.py` | 小结果保持旧协议，大结果返回 40KB 有界预览、Artifact 引用和强制读取指令 |
| `ArtifactToolMixin._artifact_search` | `backend/services/agent/artifact_tool_mixin.py` | 合并搜索当前 Run 与固定 revision 内的历史 Artifact 目录 |
| `ArtifactToolMixin._artifact_get` | `backend/services/agent/artifact_tool_mixin.py` | 获取当前 Run 或会话历史中指定 Artifact 的元数据和模型视图 |
| `ArtifactToolMixin._artifact_read` | `backend/services/agent/artifact_tool_mixin.py` | 按 cursor/max_tokens 分页读取当前或跨轮完整规范内容 |
| `build_artifact_tools` | `backend/config/artifact_tools.py` | 构建通用只读 Artifact Search/Get/Read Schema |
| `materialize_artifacts` | `backend/services/agent/runtime/artifacts/persistence.py` | Actor 提交前将 ≤64KB 完整事实内联，将更大 JSON 上传租户隔离 OSS；提交参数只携带当前 storage_kind 的有效字段 |
| `normalize_conversation_artifact_storage` | `backend/migrations/139_actor_artifact_terminal_integrity.sql` | 在数据库写入边界按 storage_kind 强制清除互斥存储字段，防止 JSON null 破坏 SQL NULL 约束 |
| `close_exhausted_actor_message` | `backend/migrations/139_actor_artifact_terminal_integrity.sql` | Actor 租约尝试耗尽时同步将 streaming assistant 消息闭合为 failed |
| `cleanup_materialized_artifacts` | `backend/services/agent/runtime/artifacts/persistence.py` | Actor 未成功提交或物化中断时 best-effort 删除本次新上传的 OSS Artifact，避免永久孤儿 |
| `build_turn_context_items` | `backend/services/agent/runtime/context/items.py` | 将输入消息、输出块和工具 Artifact 投影为有序 typed ConversationItem，保持 tool pair 原子组 |
| `assemble_history` | `backend/services/agent/runtime/context/assembler.py` | 按模型软/硬预算保留最近两个用户 Turn 与完整工具组，压缩稳定旧前缀并生成 Actor 原子提交 payload |
| `HistoryAssemblyPlan` | `backend/services/agent/runtime/context/assembler.py` | 描述 Provider 历史、持久 compaction、被覆盖 sequence 和估算 Token，与完整请求级 ContextPlan 语义分离 |
| `ContextEpoch` / `CacheIdentity` | `backend/services/agent/runtime/context/receipt.py` | 以固定前缀、base revision、动态后缀和 Tool Schema 哈希归因 Provider 缓存命中 |
| `prepare_provider_context_plan` | `backend/services/agent/runtime/context/provider_receipt.py` | 在每次 Provider 请求前构建唯一 ContextPlan 与回执；失败或投影不一致时发送前终止 |
| `prune_context` | `backend/services/agent/runtime/context/pruning.py` | 达到可用输入 50% 后，仅确定性裁剪最近 3 个用户 Turn 之前的完整 ToolResult 组，并输出不含正文的 PruningReceipt |
| `accumulate_provider_context_usage` | `backend/services/agent/runtime/context/provider_receipt.py` | 将 Provider chunk 的 prompt/completion/cache Token 累计到当前 ModelStep Receipt |
| `accumulate_usage` | `backend/services/handlers/chat/stream_session.py` | 统一累计 Run 总用量与当前 ModelStep Receipt 用量 |
| `commit_generation_turn_with_context_v2` | `backend/migrations/147_context_receipt_cache_identity.sql` | 在原子生成提交后于同一事务持久 ContextEpoch、CacheIdentity 和 ProviderUsage |
| `ProviderContextPlan.build/project/matches` | `backend/services/agent/runtime/context/provider_plan.py` | 冻结完整 ModelStep 请求并生成唯一 Provider messages/tools 投影；非无损投影直接拒绝发送 |
| `PromptBuilder.build` 稳定前缀合同 | `backend/services/prompt_builder/builder.py` | 显式输出 `stable_prefix_blocks`，经 Handler 与 RuntimeState 传入每次 Provider Receipt |
| `PromptBuilder._assemble_history` | `backend/services/prompt_builder/builder.py` | 组装预算化历史，并仅在生成新 Compaction 后刷新当前 PromptBuilder 的 Memory/Persona 快照 |
| `PromptBuilder._request_context` | `backend/services/prompt_builder/builder.py` | 复用请求级 RequestContext，缺省时按 user/org/conversation 构建本轮时间上下文 |
| `load_unified_context_messages` | `backend/services/handlers/chat_context/unified_history_loader.py` | 先加载最新有效 Compaction，再分页投影其 through_sequence 后的 ConversationItem；既有 revision 缺少统一事实时失败关闭 |
| `ChatExecutionResult` | `backend/services/handlers/chat/execution_result.py` | 通道无关的纯执行结果，携带消息块、usage、Evidence、Artifact drafts 与 ContextReceipt |
| `encode_image` | `backend/scripts/poc_ecom_requirement_assist.py` | 将本地产品图/参考图编码为多模态 data URL |
| `parse_json_response` | `backend/scripts/poc_ecom_requirement_assist.py` | 解析并清理模型返回的 JSON 方案 |
| `validate_result` | `backend/scripts/poc_ecom_requirement_assist.py` | 校验共享事实与三套创作简报的结构 |
| `evaluate_result` | `backend/scripts/poc_ecom_requirement_assist.py` | 检查结构、参考图分析、用户要求和参考商品污染 |
| `build_messages` | `backend/scripts/poc_ecom_requirement_assist.py` | 构造明确区分产品图和参考图的多模态消息 |
| `run_scenario` | `backend/scripts/poc_ecom_requirement_assist.py` | 执行单次真实 VL 模型验证并记录用量和评估 |
| `parse_args` | `backend/scripts/poc_ecom_requirement_assist.py` | 解析隔离 POC 的命令行参数 |
| `main` | `backend/scripts/poc_ecom_requirement_assist.py` | 依次运行仅文字、仅参考图和组合输入三种场景 |
| `RequirementAssistInput.validate_total_images` | `backend/schemas/ecom_requirement.py` | 校验产品图与参考图合计不超过 9 张 |
| `RequirementAssistResult.validate_suggestion_ids` | `backend/schemas/ecom_requirement.py` | 校验三套固定策略方案完整且不重复 |
| `build_context_prompt` | `backend/services/agent/image/requirement_assist_prompts.py` | 构造表单快照、用户原文和图片角色上下文 |
| `build_multimodal_messages` | `backend/services/agent/image/requirement_assist_prompts.py` | 构造明确区分产品图与参考图的多模态消息 |
| `DetailProjectService.get_ai_input_project` | `backend/services/detail_project_service.py` | 校验项目归属、产品图必填和全部图片就绪后返回 AI 输入草稿 |
| `DetailProjectRequirementAdapter.adapt` | `backend/services/agent/image/input_adapters.py` | 将详情项目图片和表单快照转换为共享 AI 帮写输入 |
| `RequirementAssistService.generate` | `backend/services/agent/image/requirement_assist_service.py` | 在 100 秒总预算内调用主/备模型并返回安全三方案 |
| `parse_requirement_result` | `backend/services/agent/image/requirement_assist_service.py` | 解析模型 JSON 并执行三方案 Schema 校验 |
| `validate_reference_ids` | `backend/services/agent/image/requirement_assist_service.py` | 禁止模型引用输入集合之外的参考图 |
| `validate_no_output_urls` | `backend/services/agent/image/requirement_assist_service.py` | 禁止模型在通用创作简报中生成外部 URL |
| `apply_conflict_gate` | `backend/services/agent/image/requirement_assist_service.py` | 从可执行简报移除冲突卖点和事实规避表达 |
| `generate_requirement_suggestions` | `backend/api/routes/ecom_requirement.py` | 适配详情项目输入并返回三套安全通用创作简报 |
| `RequirementAssistRateLimiter.check` | `backend/services/agent/image/requirement_assist_rate_limiter.py` | 使用 Redis 在多 worker 间执行每用户每分钟 5 次的原子限流 |
| `buildRequirementSuggestionsRequest` | `frontend/src/services/ecomRequirement.ts` | 将详情页表单转换为 AI 帮写后端设置快照 |
| `generateRequirementSuggestions` | `frontend/src/services/ecomRequirement.ts` | 调用可取消的三方案接口并使用 105 秒独立超时 |
| `useDetailRequirementAssist` | `frontend/src/hooks/useDetailRequirementAssist.ts` | 管理 AI 帮写弹窗、请求取消、过期响应隔离和三套独立编辑状态 |
| `RequirementAssistModal` | `frontend/src/components/detail-page/RequirementAssistModal.tsx` | 展示产品事实、参考图分析、冲突和三套可编辑 AI 帮写方案 |
