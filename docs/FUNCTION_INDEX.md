# 函数索引 (FUNCTION_INDEX)

> 本文档记录项目中所有函数的索引信息，包括函数名、文件路径、功能描述等。

## 更新规则
- 新增函数时必须同步更新本文档
- 修改函数签名时必须更新对应条目
- 删除函数时必须从本文档移除

## 函数列表

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

### 消息服务模块 (Message Service)

#### 后端函数

| 函数名 | 文件路径 | 功能描述 | 参数 | 返回值 |
|--------|----------|----------|------|--------|
| `generate_message` | `backend/api/routes/message.py` | 统一消息生成入口（send/retry/regenerate） | body: GenerateRequest | GenerateResponse |
| `MessageResponse.parse_generation_params` | `backend/schemas/message.py` | Supabase JSONB 字符串自动转 dict（field_validator） | v: Any | Any |
| `get_messages` | `backend/services/message_service.py` | 获取对话消息列表 | conversation_id, user_id, limit, offset, before_id | dict |
| `delete_message` | `backend/services/message_service.py` | 删除单条消息（权限验证后物理删除） | message_id, user_id | dict |
| `create_message` | `backend/services/message_service.py` | 创建消息记录 | conversation_id, user_id, content, role, credits_cost | dict |
| `ChatHandler.start` | `backend/services/handlers/chat_handler.py` | 启动聊天任务（smart mode 时 deferred routing） | message_id, conversation_id, user_id, content, params | task_id |
| `ChatRoutingMixin._route_and_stream` | `backend/services/handlers/chat_routing_mixin.py` | Smart mode 异步路由：Agent Loop + 记忆并行，路由完成后分发 | task_id, message_id, conversation_id, user_id, content, _params, metadata | None |
| `ChatRoutingMixin._reroute_to_media` | `backend/services/handlers/chat_routing_mixin.py` | 重路由到 Image/Video Handler（非 chat 路由结果） | task_id, message_id, ..., gen_type, model_id | None |
| `ImageHandler.start` | `backend/services/handlers/image_handler.py` | 启动图片生成任务（异步） | message_id, conversation_id, user_id, content, params | task_id |
| `VideoHandler.start` | `backend/services/handlers/video_handler.py` | 启动视频生成任务（异步） | message_id, conversation_id, user_id, content, params | task_id |
| `_reset_message_for_retry` | `backend/api/routes/message.py` | 重置失败消息用于重试 | db, message_id, gen_type, model, params | Message |
| `_create_assistant_placeholder` | `backend/api/routes/message.py` | 创建助手消息占位符 | db, conversation_id, message_id, gen_type, model, params | Message |
| `handle_regenerate_single_operation` | `backend/api/routes/message_generation_helpers.py` | 单图重新生成操作（复用现有消息，仅更新指定 image_index） | db, body, user_id | dict |

#### 前端函数

| 函数名 | 文件路径 | 功能描述 | 参数 | 返回值 |
|--------|----------|----------|------|--------|
| `sendMessage` | `frontend/src/services/messageSender.ts` | 统一消息发送（send/retry/regenerate） | options: SendOptions | Promise<string> |
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
| `handle_webhook` | `backend/api/routes/webhook.py` | 统一 Webhook 入口（按 provider 分发） | provider: str, request, db | JSONResponse |
| `TaskCompletionService.__init__` | `backend/services/task_completion_service.py` | 初始化统一任务完成服务 | db: Client | - |
| `TaskCompletionService.get_task` | `backend/services/task_completion_service.py` | 根据 external_task_id 查询任务 | external_task_id: str | Optional[Dict] |
| `TaskCompletionService.process_result` | `backend/services/task_completion_service.py` | 统一处理入口（幂等，分发成功/失败） | external_task_id, result: TaskResult | bool |
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
| `BaseHandler._build_callback_url` | `backend/services/handlers/base.py` | 构建 Webhook 回调 URL（未配置则返回 None） | provider_value: str | Optional[str] |
| `BatchCompletionService.handle_image_complete` | `backend/services/batch_completion_service.py` | 处理单个图片 task 成功（确认积分、推送 partial update、finalize） | task, content_parts | bool |
| `BatchCompletionService.handle_image_failure` | `backend/services/batch_completion_service.py` | 处理单个图片 task 失败（退回积分、推送 partial update、finalize） | task, error_code, error_message | bool |
| `BatchCompletionService._dispatch_finalize` | `backend/services/batch_completion_service.py` | 根据操作类型分发到 _finalize_batch 或 _finalize_single_image | batch_id, batch_tasks | None |
| `BatchCompletionService._finalize_single_image` | `backend/services/batch_completion_service.py` | 单图重新生成最终处理（merge-update 现有消息的 content[image_index]） | batch_id, batch_tasks | None |
| `BatchCompletionService._finalize_batch` | `backend/services/batch_completion_service.py` | 批次全部终态后最终处理（upsert 消息、推送 message_done） | batch_id, batch_tasks | None |

### KIE 适配器模块 (KIE Adapter)

#### 后端函数

| 函数名 | 文件路径 | 功能描述 | 参数 | 返回值 |
|--------|----------|----------|------|--------|
| `chat` | `backend/services/adapters/kie/chat_adapter.py` | 发送聊天请求（支持流式/非流式） | messages, stream, tools, etc | ChatCompletionChunk or AsyncIterator |
| `chat_simple` | `backend/services/adapters/kie/chat_adapter.py` | 简化聊天接口 | user_message, system_prompt, history, stream | ChatCompletionChunk or AsyncIterator |
| `estimate_cost` | `backend/services/adapters/kie/chat_adapter.py` | 估算积分消耗 | input_tokens, output_tokens | CostEstimate |
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
| `MessageItem` | `frontend/src/components/chat/MessageItem.tsx` | 单条消息（memo 包裹，useCallback 稳定回调：handleImageClick/handleRegenerateSingle/handleRegenerate） |
| `MessageMedia` | `frontend/src/components/chat/MessageMedia.tsx` | 消息媒体渲染（memo 包裹，图片、视频、懒加载、下载、右键菜单） |
| `MessageActions` | `frontend/src/components/chat/MessageActions.tsx` | 消息操作工具栏（复制、朗读、反馈、分享、删除） |
| `MessageToolbar` | `frontend/src/components/chat/MessageToolbar.tsx` | 消息工具栏（旧版） |
| `DeleteMessageModal` | `frontend/src/components/chat/DeleteMessageModal.tsx` | 删除消息确认弹框 |
| `InputArea` | `frontend/src/components/chat/InputArea.tsx` | 输入区域 |
| `InputControls` | `frontend/src/components/chat/InputControls.tsx` | 输入控制（文本框、按钮、上传） |
| `ModelSelector` | `frontend/src/components/chat/ModelSelector.tsx` | 模型选择器 |
| `AdvancedSettingsMenu` | `frontend/src/components/chat/AdvancedSettingsMenu.tsx` | 高级设置菜单 |
| `SettingsModal` | `frontend/src/components/chat/SettingsModal.tsx` | 个人设置弹框 |
| `UploadMenu` | `frontend/src/components/chat/UploadMenu.tsx` | 上传菜单 |
| `ImagePreview` | `frontend/src/components/chat/ImagePreview.tsx` | 图片预览（输入区小图预览，引用图片蓝色边框+引号图标+引用角标） |
| `ImagePreviewModal` | `frontend/src/components/chat/ImagePreviewModal.tsx` | 图片预览弹窗（全屏缩放下载） |
| `LoadingPlaceholder` | `frontend/src/components/chat/LoadingPlaceholder.tsx` | 统一加载占位符（文字 + 跳动小圆点） |
| `MediaPlaceholder` | `frontend/src/components/chat/MediaPlaceholder.tsx` | 统一媒体占位符（灰色框 + 图标，支持图片/视频/音频等） |
| `ImageContextMenu` | `frontend/src/components/chat/ImageContextMenu.tsx` | 图片右键上下文菜单（引用/复制/下载，dispatch chat:quote-image 事件） |
| `AiImageGrid` | `frontend/src/components/chat/AiImageGrid.tsx` | AI 多图网格组件（2/3/4 张自适应布局，含失败占位符、单图重新生成、右键菜单） |
| `GridCell` | `frontend/src/components/chat/AiImageGrid.tsx` | 单个网格单元（memo + gridCellAreEqual 自定义比较，仅数据 props 变化时重渲染） |
| `gridCellAreEqual` | `frontend/src/components/chat/AiImageGrid.tsx` | GridCell 自定义 memo 比较函数（比较 imageUrl/failed/index/messageId/isGenerating，忽略函数引用） |
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

> **新增于记忆智能过滤**：Mem0 向量检索 + 千问二次精排，两级过滤确保注入上下文的记忆高度相关。

#### 后端函数

| 函数名 | 文件路径 | 功能描述 | 参数 | 返回值 |
|--------|----------|----------|------|--------|
| `MemoryService.get_relevant_memories` | `backend/services/memory_service.py` | 检索相关记忆（两级过滤：Mem0 阈值初筛 → 千问精排） | user_id, query, limit | List[Dict] |
| `MemoryService.extract_memories_from_conversation` | `backend/services/memory_service.py` | 从对话中自动提取记忆（Mem0 LLM） | user_id, messages, conversation_id | List[Dict] |
| `MemoryService.get_all_memories` | `backend/services/memory_service.py` | 获取用户所有记忆（带内存缓存） | user_id | List[Dict] |
| `MemoryService.add_memory` | `backend/services/memory_service.py` | 添加记忆 | user_id, content, source | List[Dict] |
| `MemoryService.is_memory_enabled` | `backend/services/memory_service.py` | 检查用户是否开启记忆 | user_id | bool |
| `filter_memories` | `backend/services/memory_filter.py` | 千问精排过滤（降级链：turbo → plus → 跳过） | query, memories | List[Dict] |
| `format_memory` | `backend/services/memory_config.py` | 格式化单条 Mem0 记忆（含 score） | raw | Dict |
| `build_memory_system_prompt` | `backend/services/memory_config.py` | 将记忆列表构建为 system prompt | memories | str |
| `ChatContextMixin._build_memory_prompt` | `backend/services/handlers/chat_context_mixin.py` | 构建记忆 system prompt（对话注入入口） | user_id, query | Optional[str] |
| `ChatContextMixin._extract_memories_async` | `backend/services/handlers/chat_context_mixin.py` | 异步提取记忆（fire-and-forget） | user_id, conversation_id, user_text, assistant_text | None |

#### 前端函数

| 函数名 | 文件路径 | 功能描述 | 参数 | 返回值 |
|--------|----------|----------|------|--------|
| `useMemoryStore` | `frontend/src/stores/useMemoryStore.ts` | Zustand 记忆状态管理（CRUD + 设置） | - | MemoryStore |
| `getMemories` | `frontend/src/services/memory.ts` | 获取记忆列表 | - | Promise |
| `addMemory` | `frontend/src/services/memory.ts` | 添加记忆 | content | Promise |

#### 配置常量

| 常量名 | 值 | 文件路径 | 说明 |
|--------|-----|----------|------|
| `MEMORY_SEARCH_THRESHOLD` | 0.5 | `backend/services/memory_config.py` | Mem0 向量搜索相似度阈值 |
| `MAX_INJECTION_COUNT` | 20 | `backend/services/memory_config.py` | 单次对话注入最大记忆数 |
| `MAX_MEMORIES_PER_USER` | 100 | `backend/services/memory_config.py` | 每用户记忆上限 |
| `MEM0_TIMEOUT` | 45s | `backend/services/memory_config.py` | Mem0 操作超时 |
| `memory_filter_model` | qwen-turbo | `backend/core/config.py` | 记忆精排主模型 |
| `memory_filter_fallback_model` | qwen-plus | `backend/core/config.py` | 记忆精排备用模型 |
| `memory_filter_timeout` | 3.0s | `backend/core/config.py` | 精排单次超时 |

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
| `IntentRouter._record_routing_signal` | `backend/services/intent_router.py` | 记录意图路由决策信号（fire-and-forget） | decision, user_id, input_length, has_image, router_model | None |
| `AgentLoop.run` | `backend/services/agent_loop.py` | 执行 Agent Loop，返回路由结果 | content, thinking_mode?, task_id? | AgentResult |
| `AgentLoop._record_loop_signal` | `backend/services/agent_loop.py` | 记录 Agent Loop 路由信号（含 loop_turns/tokens） | result, input_length, has_image | None |
| `_record_user_feedback_signal` | `backend/api/routes/message.py` | 记录用户反馈信号（retry/regenerate/regenerate_single） | db, user_id, operation, model, gen_type, original_message_id, conversation_id | None |
| `MemoryService._record_memory_search_signal` | `backend/services/memory_service.py` | 记录记忆检索效果信号（mem0_returned/filtered_count/latency） | user_id, mem0_returned, filtered_count, filter_latency_ms, query_length | None |

#### 信号类型（task_type 值）

| task_type | 来源 | 关键 params 字段 |
|-----------|------|-----------------|
| `image` / `video` | 成功/失败回调 | cost_time_ms, retried, retry_from_model |
| `routing` | IntentRouter / AgentLoop | routing_tool, routed_by, recommended_model, input_length, has_image |
| `user_feedback` | message.py 操作分发 | feedback_type, original_model, new_model, original_task_type |
| `memory_search` | MemoryService | mem0_returned, filtered_count, filter_latency_ms, query_length |

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
| `format_message` | `backend/services/message_utils.py` | 格式化消息响应 | message_data | dict |
| `deduct_user_credits` | `backend/services/message_utils.py` | 扣除用户积分 | db, user_id, credits, description | None |
| `_generate_with_credits` | `backend/services/video_service.py` | 通用视频生成流程 | user_id, model, ... | Dict[str, Any] |
| `_get_user` | `backend/services/base_generation_service.py` | 获取用户信息 | user_id | dict |
| `_check_credits` | `backend/services/base_generation_service.py` | 检查积分是否足够 | user, required_credits | None |
| `_deduct_credits` | `backend/services/base_generation_service.py` | 扣除积分 | user_id, credits, description | int |

### 企业微信 AI 路由模块 (WeChat Work AI Routing)

#### 后端函数 — WecomAIMixin (`backend/services/wecom/wecom_ai_mixin.py`)

| 函数名 | 文件路径 | 功能描述 | 参数 | 返回值 |
|--------|----------|----------|------|--------|
| `_run_agent_loop` | `backend/services/wecom/wecom_ai_mixin.py` | Agent Loop 路由（失败降级 IntentRouter → 兜底 CHAT） | user_id, conversation_id, content | AgentResult |
| `_build_memory_prompt` | `backend/services/wecom/wecom_ai_mixin.py` | 构建记忆 system prompt | user_id, query | Optional[str] |
| `_handle_chat_response` | `backend/services/wecom/wecom_ai_mixin.py` | 处理 CHAT 类型（direct_reply 或流式生成） | user_id, conversation_id, message_id, text_content, reply_ctx, agent_result, memory_prompt | None |
| `_handle_image_response` | `backend/services/wecom/wecom_ai_mixin.py` | 处理 IMAGE 类型（积分检查 → 生成 → 发送） | user_id, conversation_id, message_id, text_content, reply_ctx, agent_result | None |
| `_handle_video_response` | `backend/services/wecom/wecom_ai_mixin.py` | 处理 VIDEO 类型（积分检查 → 生成 → 发送） | user_id, conversation_id, message_id, text_content, reply_ctx, agent_result | None |
| `_send_media_to_wecom` | `backend/services/wecom/wecom_ai_mixin.py` | 统一媒体发送（两渠道差异封装）+ 更新 DB | reply_ctx, urls, media_type, message_id | None |
| `_handle_chat_fallback` | `backend/services/wecom/wecom_ai_mixin.py` | 兜底：默认模型直接聊天 | user_id, conversation_id, message_id, text_content, reply_ctx | None |
| `_stream_and_reply` | `backend/services/wecom/wecom_ai_mixin.py` | 流式生成 + 推送到企微 + 更新 DB | adapter, messages, reply_ctx, message_id | None |
| `_build_chat_messages` | `backend/services/wecom/wecom_ai_mixin.py` | 构建 LLM 消息列表（记忆/人设/搜索上下文） | user_id, conversation_id, text_content, system_prompt, memory_prompt, search_context | List[Dict] |
| `_get_user_balance` | `backend/services/wecom/wecom_ai_mixin.py` | 获取用户积分余额 | user_id | int |
| `_deduct_credits` | `backend/services/wecom/wecom_ai_mixin.py` | 直接扣除积分 | user_id, amount, reason | None |

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
- **KIE 适配器模块**：5个后端函数
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
| `KernelManager` | `backend/services/sandbox/kernel_manager.py` | Kernel进程池管理器（创建/复用/回收/降级） |
| `KernelManager.get_or_create` | `backend/services/sandbox/kernel_manager.py` | 获取或创建Kernel（超限降级返回False） |
| `KernelManager.execute` | `backend/services/sandbox/kernel_manager.py` | 向Kernel发送代码并等待结果 |
| `kernel_main` | `backend/services/sandbox/kernel_worker.py` | Kernel Worker REPL主循环（stdin/stdout JSON-Line） |
| `build_sandbox_executor` | `backend/services/sandbox/functions.py` | 工厂函数（构建执行器+注入KernelManager） |
| `validate_code` | `backend/services/sandbox/validators.py` | AST安全预检（模块/函数黑名单+dunder限制） |
| `get_kernel_manager` | `backend/services/sandbox/kernel_manager.py` | 获取全局KernelManager单例 |
