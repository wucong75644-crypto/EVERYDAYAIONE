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

#### 前端函数

| 函数名 | 文件路径 | 功能描述 | 参数 | 返回值 |
|--------|----------|----------|------|--------|
| `useTaskStore` | `frontend/stores/taskStore.ts` | Zustand全局任务状态管理 | - | TaskStore |
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
| `send_message` | `backend/services/message_service.py` | 发送消息并获取 AI 响应（非流式） | conversation_id, user_id, content, model_id | dict |
| `send_message_stream` | `backend/services/message_stream_service.py` | 流式发送消息并获取 AI 响应（SSE） | conversation_id, user_id, content, model_id | AsyncIterator[str] |
| `regenerate_message_stream` | `backend/services/message_stream_service.py` | 重新生成失败的消息（流式） | conversation_id, message_id, user_id | AsyncIterator[str] |
| `_call_ai_chat` | `backend/services/message_service.py` | 调用 AI Chat 模型（非流式） | conversation_id, user_id, user_message, model_id | tuple[str, int] |
| `_get_conversation_history` | `backend/services/message_service.py` | 获取对话历史用于 AI 上下文 | conversation_id, user_id, limit | List[Dict] |
| `create_message` | `backend/services/message_service.py` | 创建消息记录 | conversation_id, user_id, content, role, credits_cost | dict |
| `get_messages` | `backend/services/message_service.py` | 获取对话消息列表 | conversation_id, user_id, limit, offset, before_id | dict |
| `delete_message` | `backend/services/message_service.py` | 删除单条消息（权限验证后物理删除） | message_id, user_id | dict |
| `create_error_message` | `backend/services/message_service.py` | 创建错误消息记录（AI 调用失败时） | conversation_id, user_id, content | dict |
| `regenerate_message_stream` | `backend/services/message_service.py` | 重新生成失败消息（流式 SSE） | conversation_id, message_id, user_id | AsyncIterator[str] |

#### 前端函数

| 函数名 | 文件路径 | 功能描述 | 参数 | 返回值 |
|--------|----------|----------|------|--------|
| `sendMessage` | `frontend/src/services/message.ts` | 发送消息（非流式） | conversationId, data | Promise<SendMessageResponse> |
| `sendMessageStream` | `frontend/src/services/message.ts` | 流式发送消息（SSE） | conversationId, data, callbacks | Promise<void> |
| `getMessages` | `frontend/src/services/message.ts` | 获取消息列表 | conversationId, limit, offset, beforeId | Promise<MessageListResponse> |
| `deleteMessage` | `frontend/src/services/message.ts` | 删除单条消息 | messageId | Promise<DeleteMessageResponse> |
| `regenerateMessageStream` | `frontend/src/services/message.ts` | 重新生成失败消息（流式 SSE） | conversationId, messageId, callbacks | Promise<void> |
| `handleRegenerate` | `frontend/src/components/chat/MessageArea.tsx` | 处理消息重新生成请求 | messageId | Promise<void> |

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

| 函数名 | 文件路径 | 功能描述 | 参数 | 返回值 |
|--------|----------|----------|------|--------|
| `useChatStore` | `frontend/src/stores/useChatStore.ts` | Zustand 聊天状态管理 | - | ChatState |
| `setConversations` | `frontend/src/stores/useChatStore.ts` | 设置对话列表 | conversations | void |
| `setCurrentConversation` | `frontend/src/stores/useChatStore.ts` | 设置当前对话 | id, title | void |
| `setMessages` | `frontend/src/stores/useChatStore.ts` | 设置消息列表 | messages | void |
| `addMessage` | `frontend/src/stores/useChatStore.ts` | 添加单条消息 | message | void |
| `deleteConversation` | `frontend/src/stores/useChatStore.ts` | 删除对话 | id | void |
| `renameConversation` | `frontend/src/stores/useChatStore.ts` | 重命名对话 | id, title | void |
| `createConversation` | `frontend/src/stores/useChatStore.ts` | 创建新对话 | title | string |
| `formatDateGroup` | `frontend/src/components/chat/conversationUtils.ts` | 格式化日期分组（今天/昨天/具体日期） | dateStr | string |
| `groupConversationsByDate` | `frontend/src/components/chat/conversationUtils.ts` | 按日期分组对话列表 | conversations | Record |

### 任务状态管理模块 (Task Store)

#### 前端函数

| 函数名 | 文件路径 | 功能描述 | 参数 | 返回值 |
|--------|----------|----------|------|--------|
| `useTaskStore` | `frontend/src/stores/useTaskStore.ts` | Zustand 任务状态管理 | - | TaskStore |
| `startTask` | `frontend/src/stores/useTaskStore.ts` | 开始任务 | conversationId, type | void |
| `updateTaskProgress` | `frontend/src/stores/useTaskStore.ts` | 更新任务进度 | conversationId, progress, content | void |
| `completeTask` | `frontend/src/stores/useTaskStore.ts` | 完成任务 | conversationId, result | void |
| `failTask` | `frontend/src/stores/useTaskStore.ts` | 任务失败 | conversationId, error | void |
| `canStartTask` | `frontend/src/stores/useTaskStore.ts` | 检查是否可以开始新任务 | conversationId | { allowed, reason? } |
| `getActiveTaskCount` | `frontend/src/stores/useTaskStore.ts` | 获取活跃任务数量 | - | number |

### 对话运行时状态模块 (Conversation Runtime Store)

#### 前端函数

| 函数名 | 文件路径 | 功能描述 | 参数 | 返回值 |
|--------|----------|----------|------|--------|
| `useConversationRuntimeStore` | `frontend/src/stores/useConversationRuntimeStore.ts` | 对话运行时状态管理 | - | RuntimeStore |

### 自定义 Hooks 模块 (Custom Hooks)

#### 前端函数

| 函数名 | 文件路径 | 功能描述 | 参数 | 返回值 |
|--------|----------|----------|------|--------|
| `useClickOutside` | `frontend/src/hooks/useClickOutside.ts` | 点击外部关闭逻辑 | ref, isVisible, onClose, skipCondition | void |
| `useMessageLoader` | `frontend/src/hooks/useMessageLoader.ts` | 消息加载（含缓存） | options | { messages, loading, loadMessages, ... } |
| `useMessageHandlers` | `frontend/src/hooks/useMessageHandlers.ts` | 消息发送处理 | options | { handleSendMessage, isProcessing, ... } |
| `useRegenerateHandlers` | `frontend/src/hooks/useRegenerateHandlers.ts` | 消息重新生成处理 | options | { handleRegenerate, isRegenerating, ... } |
| `useModelSelection` | `frontend/src/hooks/useModelSelection.ts` | 模型选择逻辑 | options | { selectedModel, setSelectedModel, ... } |
| `useImageUpload` | `frontend/src/hooks/useImageUpload.ts` | 图片上传逻辑 | - | { uploadImage, uploading, ... } |
| `useAudioRecording` | `frontend/src/hooks/useAudioRecording.ts` | 录音逻辑 | - | { startRecording, stopRecording, ... } |
| `useDragDropUpload` | `frontend/src/hooks/useDragDropUpload.ts` | 拖拽上传逻辑 | - | { isDragging, handleDrop, ... } |
| `useScrollManager` | `frontend/src/hooks/useScrollManager.ts` | 滚动管理逻辑 | - | { scrollToBottom, ... } |

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
| `MessageItem` | `frontend/src/components/chat/MessageItem.tsx` | 单条消息（组合 MessageMedia 和 MessageActions） |
| `MessageMedia` | `frontend/src/components/chat/MessageMedia.tsx` | 消息媒体渲染（图片、视频、懒加载、下载） |
| `MessageActions` | `frontend/src/components/chat/MessageActions.tsx` | 消息操作工具栏（复制、朗读、反馈、分享、删除） |
| `MessageToolbar` | `frontend/src/components/chat/MessageToolbar.tsx` | 消息工具栏（旧版） |
| `DeleteMessageModal` | `frontend/src/components/chat/DeleteMessageModal.tsx` | 删除消息确认弹框 |
| `InputArea` | `frontend/src/components/chat/InputArea.tsx` | 输入区域 |
| `InputControls` | `frontend/src/components/chat/InputControls.tsx` | 输入控制（文本框、按钮、上传） |
| `ModelSelector` | `frontend/src/components/chat/ModelSelector.tsx` | 模型选择器 |
| `AdvancedSettingsMenu` | `frontend/src/components/chat/AdvancedSettingsMenu.tsx` | 高级设置菜单 |
| `SettingsModal` | `frontend/src/components/chat/SettingsModal.tsx` | 个人设置弹框 |
| `UploadMenu` | `frontend/src/components/chat/UploadMenu.tsx` | 上传菜单 |
| `ImagePreview` | `frontend/src/components/chat/ImagePreview.tsx` | 图片预览（输入区小图预览） |
| `ImagePreviewModal` | `frontend/src/components/chat/ImagePreviewModal.tsx` | 图片预览弹窗（全屏缩放下载） |
| `MediaPlaceholder` | `frontend/src/components/chat/MediaPlaceholder.tsx` | 媒体占位符（加载中状态） |
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

### 后端服务辅助模块 (Backend Service Helpers)

#### 后端函数

| 函数名 | 文件路径 | 功能描述 | 参数 | 返回值 |
|--------|----------|----------|------|--------|
| `format_message` | `backend/services/message_utils.py` | 格式化消息响应 | message_data | dict |
| `deduct_user_credits` | `backend/services/message_utils.py` | 扣除用户积分 | db, user_id, credits, description | None |
| `prepare_ai_stream_client` | `backend/services/message_ai_helpers.py` | 准备 AI 流式客户端 | model_id | tuple[model, client, adapter] |
| `stream_ai_response` | `backend/services/message_ai_helpers.py` | 流式获取 AI 响应 | adapter, history_func, ... | AsyncIterator |
| `_generate_with_credits` | `backend/services/video_service.py` | 通用视频生成流程 | user_id, model, ... | Dict[str, Any] |
| `_get_user` | `backend/services/base_generation_service.py` | 获取用户信息 | user_id | dict |
| `_check_credits` | `backend/services/base_generation_service.py` | 检查积分是否足够 | user, required_credits | None |
| `_deduct_credits` | `backend/services/base_generation_service.py` | 扣除积分 | user_id, credits, description | int |

---

## 函数分类索引

### 按模块分类
- **Redis 基础设施模块**：6个后端函数（✨新增）
- **任务限制服务模块**：4个后端函数（✨新增）
- **任务管理模块**：9个后端函数 + 8个前端函数
- **积分管理模块**：7个后端函数（✨更新）
- **对话管理模块**：5个后端函数 + 5个前端函数
- **消息服务模块**：8个后端函数 + 5个前端函数
- **图像生成模块**：3个后端函数 + 5个前端函数
- **视频生成模块**：5个后端函数 + 6个前端函数
- **用户设置模块**：3个前端函数
- **KIE 适配器模块**：5个后端函数
- **总计**：约 90 个函数

### 按功能分类
- **Redis 操作**：`RedisClient.get_client`, `RedisClient.acquire_lock`, `RedisClient.release_lock`
- **任务限制**：`check_and_acquire`, `release`, `get_active_count`, `can_start_task`
- **任务创建与提交**：`create_task`, `submitTask`, `checkTaskLimits`
- **任务状态管理**：`update_task_status`, `handle_task_completion`, `handle_task_failure`, `useTaskStore`, `mergeTasks`
- **任务查询**：`get_active_tasks`, `count_active_tasks`, `count_conversation_active_tasks`, `getConversationTaskBadge`
- **AI调用**：`call_ai_api`, `process_task_worker`
- **实时通信**：`subscribeTaskUpdates`, `handleTaskProgress`, `handleTaskCompleted`
- **积分操作**：`lock_credits`, `confirm_deduct`, `refund_credits`, `credit_lock`, `deduct_atomic`, `get_balance`
- **对话管理**：`create_conversation`, `update_conversation_title`, `get_conversation_list`, `delete_conversation`
- **标题管理**：`generate_auto_title`, `generateAutoTitle`, `updateConversationTitle`, `syncTitleToNavbar`, `handleTitleEdit`

---

## 统计信息
- **总函数数**：约 130 个（规划中 + 已实现）
- **已实现组件**：26 个聊天组件
- **已实现 Hooks**：9 个自定义 Hooks
- **已实现模块**：Redis 基础设施、任务限制服务、积分服务、消息服务、图像生成、视频生成、用户设置、KIE 适配器、聊天模块、任务状态管理
- **最后更新**：2026-01-27（添加 Redis 基础设施模块、任务限制服务模块、更新积分管理模块）
