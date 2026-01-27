# 技术方案：单对话多任务并发

## 背景
当前图片/视频生成任务在轮询完成前会阻塞输入框，需要改为"提交即解锁"模式支持并发。

## 改造范围

### Phase 1: useChatStore 缓存安全改造
**目标**：缓存更新改为函数式 set，避免并发覆盖

**改动点**：
- `updateCachedMessages`: 改为函数式 set
- `addMessageToCache`: 改为函数式 set
- 新增 `replaceMessageInCache(conversationId, messageId, newMessage)`: 原子替换单条消息

### Phase 2: useTaskStore 改造
**目标**：支持 taskId 作为 key + 轮询生命周期管理

**改动点**：
- `activeTasks`: `Map<conversationId, Task>` → `Map<taskId, Task>`
- Task 结构新增：`taskId`, `type: 'image' | 'video'`, `placeholderId`
- 新增 `pollingIntervals: Map<taskId, number>` 管理轮询定时器
- 新增方法：
  - `startPolling(taskId, pollFn, callbacks)`
  - `stopPolling(taskId)`
  - `getTasksByConversation(conversationId)`: 获取某对话的所有任务

### Phase 3: useMessageHandlers 提交即返回
**目标**：图片/视频任务提交成功后立即返回，轮询在后台运行

**改动点**：
- `handleImageGeneration`: 提交后调用 `taskStore.startPolling()`，不 await 轮询
- `handleVideoGeneration`: 同上
- 轮询完成后通过回调更新消息

### Phase 4: useRegenerateHandlers 同步修改
**目标**：重新生成逻辑与新发送保持一致

**改动点**：
- `regenerateImageMessage`: 同 Phase 3
- `regenerateVideoMessage`: 同 Phase 3

## 数据流设计

```
用户发送图片任务
    ↓
InputArea.handleSubmit()
    ↓
useMessageHandlers.handleImageGeneration()
    ├── 1. 创建用户消息（保存到DB）
    ├── 2. 创建占位符消息 streaming-${taskId}
    ├── 3. 调用 API 获取 taskId
    ├── 4. taskStore.startPolling(taskId, ...)  ← 不 await
    └── 5. 立即返回，解锁输入框

后台轮询（在 taskStore 管理）
    ├── 定时调用 pollTaskUntilDone
    ├── 完成时：
    │   ├── 保存 AI 消息到 DB
    │   ├── chatStore.replaceMessageInCache(placeholderId, realMessage)
    │   ├── taskStore.completeTask(taskId)
    │   └── 刷新积分
    └── 失败时：
        ├── 保存错误消息到 DB
        ├── chatStore.replaceMessageInCache(placeholderId, errorMessage)
        └── taskStore.failTask(taskId)
```

## 消息顺序处理
- 按 `created_at` 时间排序显示
- 占位符消息使用发送时的时间戳，确保顺序正确

## 页面刷新处理
- 轮询丢失可接受
- 后端任务仍会完成
- 刷新后加载消息可看到结果

## 创建时间
2026-01-26
