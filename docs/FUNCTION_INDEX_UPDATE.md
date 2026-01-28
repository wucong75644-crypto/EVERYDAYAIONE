# FUNCTION_INDEX.md 更新内容

## 新增模块和函数

### 消息处理模块 (Message Handlers)

#### 前端函数

| 函数名 | 文件路径 | 功能描述 | 参数 | 返回值 |
|--------|----------|----------|------|--------|
| `useMessageHandlers` | `frontend/src/hooks/useMessageHandlers.ts` | 消息处理器组合 Hook | UseMessageHandlersParams | {handleChatMessage, handleImageGeneration, handleVideoGeneration} |
| `useTextMessageHandler` | `frontend/src/hooks/handlers/useTextMessageHandler.ts` | 文本消息处理 Hook | UseTextMessageHandlerParams | {handleChatMessage} |
| `useImageMessageHandler` | `frontend/src/hooks/handlers/useImageMessageHandler.ts` | 图片消息处理 Hook | UseImageMessageHandlerParams | {handleImageGeneration} |
| `useVideoMessageHandler` | `frontend/src/hooks/handlers/useVideoMessageHandler.ts` | 视频消息处理 Hook | UseVideoMessageHandlerParams | {handleVideoGeneration} |
| `extractErrorMessage` | `frontend/src/hooks/handlers/mediaHandlerUtils.ts` | 从错误对象提取友好消息 | error: unknown | string |
| `extractImageUrl` | `frontend/src/hooks/handlers/mediaHandlerUtils.ts` | 从 API 响应提取图片 URL | result: unknown | string \| undefined |
| `extractVideoUrl` | `frontend/src/hooks/handlers/mediaHandlerUtils.ts` | 从 API 响应提取视频 URL | result: unknown | string \| undefined |
| `handleGenerationError` | `frontend/src/hooks/handlers/mediaHandlerUtils.ts` | 处理生成错误并创建错误消息 | conversationId, errorPrefix, error, createdAt?, generationParams? | Promise<Message> |

### 滚动管理模块 (Scroll Management)

#### 前端函数

| 函数名 | 文件路径 | 功能描述 | 参数 | 返回值 |
|--------|----------|----------|------|--------|
| `useMessageAreaScroll` | `frontend/src/hooks/useMessageAreaScroll.ts` | 消息区域滚动管理组合 Hook | UseMessageAreaScrollOptions | {hasScrolledForConversation, handleRegenerateScroll} |
| `useConversationSwitchScroll` | `frontend/src/hooks/scroll/useConversationSwitchScroll.ts` | 对话切换滚动管理 | UseConversationSwitchScrollOptions | void |
| `useMessageLoadingScroll` | `frontend/src/hooks/scroll/useMessageLoadingScroll.ts` | 消息加载完成滚动定位 | UseMessageLoadingScrollOptions | void |
| `useNewMessageScroll` | `frontend/src/hooks/scroll/useNewMessageScroll.ts` | 新消息添加自动滚动 | UseNewMessageScrollOptions | void |
| `useStreamingScroll` | `frontend/src/hooks/scroll/useStreamingScroll.ts` | 流式内容更新滚动跟随 | UseStreamingScrollOptions | void |
| `useMediaReplacementScroll` | `frontend/src/hooks/scroll/useMediaReplacementScroll.ts` | 媒体内容替换滚动 | UseMediaReplacementScrollOptions | void |

### 重新生成模块 (Regenerate)

#### 前端函数

| 函数名 | 文件路径 | 功能描述 | 参数 | 返回值 |
|--------|----------|----------|------|--------|
| `useRegenerateHandlers` | `frontend/src/hooks/useRegenerateHandlers.ts` | 重新生成处理器组合 Hook | RegenerateHandlersOptions | {regenerateFailedMessage, regenerateAsNewMessage, regenerateImageMessage, regenerateVideoMessage} |
| `useRegenerateFailedMessage` | `frontend/src/hooks/regenerate/useRegenerateFailedMessage.ts` | 失败消息原地重新生成 | UseRegenerateFailedMessageOptions | (messageId, targetMessage) => Promise<void> |
| `useRegenerateAsNewMessage` | `frontend/src/hooks/regenerate/useRegenerateAsNewMessage.ts` | 成功消息新增对话重新生成 | UseRegenerateAsNewMessageOptions | (userMessage) => Promise<void> |

### 轮询管理模块 (Polling)

#### 前端函数

| 函数名 | 文件路径 | 功能描述 | 参数 | 返回值 |
|--------|----------|----------|------|--------|
| `PollingManager` | `frontend/src/utils/polling.ts` | 轮询管理器类 | - | PollingManager |
| `PollingManager.start` | `frontend/src/utils/polling.ts` | 开始轮询任务 | taskId, pollFn, callbacks, options? | () => void (cleanup) |
| `PollingManager.stop` | `frontend/src/utils/polling.ts` | 停止轮询任务 | taskId | void |
| `PollingManager.stopAll` | `frontend/src/utils/polling.ts` | 停止所有轮询任务 | - | void |
| `PollingManager.has` | `frontend/src/utils/polling.ts` | 检查任务是否正在轮询 | taskId | boolean |
| `PollingManager.size` | `frontend/src/utils/polling.ts` | 获取当前轮询任务数量 | - | number |

### 媒体重新生成模块 (Media Regeneration)

#### 前端函数

| 函数名 | 文件路径 | 功能描述 | 参数 | 返回值 |
|--------|----------|----------|------|--------|
| `executeImageRegeneration` | `frontend/src/utils/mediaRegeneration.ts` | 执行图片重新生成流程 | ImageRegenParams | Promise<void> |
| `executeVideoRegeneration` | `frontend/src/utils/mediaRegeneration.ts` | 执行视频重新生成流程 | VideoRegenParams | Promise<void> |
| `handleMediaPolling` | `frontend/src/utils/mediaRegeneration.ts` | 处理媒体生成后台轮询 | taskId, placeholderId, creditsConsumed, config, ... | void |
| `saveUserMessage` | `frontend/src/utils/mediaRegeneration.ts` | 保存用户消息到数据库 | conversationId, userMessage, tempUserId, setMessages, createdAt | Promise<Message> |
| `getModelTypeById` | `frontend/src/utils/mediaRegeneration.ts` | 根据模型 ID 获取类型 | modelId: string | 'chat' \| 'image' \| 'video' \| null |

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

## 更新的函数

### 任务管理模块 (Task Management)

#### 前端函数 - 更新

| 函数名 | 文件路径 | 功能描述 | 参数 | 返回值 | 变更说明 |
|--------|----------|----------|------|--------|----------|
| `useTaskStore` | `frontend/src/stores/useTaskStore.ts` | Zustand全局任务状态管理 | - | TaskStore | 简化注释，使用轮询类型定义 |

## 测试相关函数

### 测试工具模块 (Testing Utils)

| 函数名 | 文件路径 | 功能描述 | 参数 | 返回值 |
|--------|----------|----------|------|--------|
| `customRender` | `frontend/src/test/testUtils.tsx` | 自定义 render 函数 | ui, options? | RenderResult |
| `customRenderHook` | `frontend/src/test/testUtils.tsx` | 自定义 renderHook 函数 | render, options? | RenderHookResult |
| `mockAsyncFn` | `frontend/src/test/testUtils.tsx` | 创建 Mock 异步函数 | value, delayMs? | MockInstance |
| `delay` | `frontend/src/test/testUtils.tsx` | 延迟工具函数 | ms | Promise<void> |

## 预定义常量

### 性能标记常量 (Performance Markers)

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

### 媒体默认值常量 (Media Defaults)

| 常量名 | 值 | 功能描述 |
|--------|-----|----------|
| `MEDIA_DEFAULTS.IMAGE_MODEL` | 'google/nano-banana' | 默认图片模型 |
| `MEDIA_DEFAULTS.VIDEO_MODEL` | 'sora-2-text-to-video' | 默认视频模型 |
| `MEDIA_DEFAULTS.I2V_MODEL` | 'sora-2-image-to-video' | 默认图生视频模型 |

## 插入位置

在 `FUNCTION_INDEX.md` 的 "前端函数" 部分，"对话管理模块" 之后添加以上所有新增模块。

## 文档说明

- 所有新增函数都已完整记录
- 包含参数类型和返回值类型
- 提供清晰的功能描述
- 标注了核心函数和工具函数
- 记录了测试相关函数
- 列出了预定义常量

## 相关文档链接

- [测试指南](../frontend/TESTING.md)
- [性能监控指南](../frontend/src/utils/PERFORMANCE_MONITORING.md)
- [Handler 使用指南](../frontend/src/hooks/handlers/README.md)
