# 消息系统数据流完整梳理

> 创建时间：2026-02-10
> 目的：系统性梳理消息从前端发送到接收的完整数据流，找出类型不匹配和状态更新的问题

---

## 📋 问题现象

1. **500 错误**：datetime 序列化失败（已修复）
2. **光标一直闪动**：消息状态未从 `streaming` 更新为 `completed`
3. **刷新后显示占位符**：消息内容丢失，只显示"AI 正在思考..."

---

## 🔍 数据流追踪

### 1️⃣ 前端发送阶段

**文件**: `frontend/src/services/messageSender.ts`

**发送的数据结构**:
```typescript
interface GenerateRequest {
  content: ContentPart[];              // 消息内容
  operation: MessageOperation;         // 操作类型：send/retry/regenerate
  model?: string;                      // 模型 ID
  params?: Record<string, any>;        // 业务参数
  client_task_id?: string;             // 前端生成的任务 ID
  placeholder_created_at?: string;     // 占位符创建时间（ISO 字符串）
  assistant_message_id?: string;       // 助手消息 ID
  original_message_id?: string;        // 原消息 ID（retry/regenerate）
  client_request_id?: string;          // 请求 ID
}
```

**实际发送示例**:
```json
{
  "content": [{"type": "text", "text": "你好"}],
  "operation": "send",
  "model": "gemini-2.5-flash",
  "params": {},
  "client_task_id": "xxx-xxx-xxx",
  "placeholder_created_at": "2026-02-10T08:00:00.000Z",
  "assistant_message_id": "yyy-yyy-yyy"
}
```

---

### 2️⃣ 后端接收阶段

**文件**: `backend/api/routes/message.py` - `generate_message()`

**接收的 Pydantic 模型**:
```python
class GenerateRequest(BaseModel):
    content: List[ContentPart]
    operation: MessageOperation = MessageOperation.SEND
    model: Optional[str] = None
    params: Optional[Dict[str, Any]] = None
    client_task_id: Optional[str] = None
    placeholder_created_at: Optional[datetime] = None  # ⚠️ 自动转换为 datetime
    assistant_message_id: Optional[str] = None
    original_message_id: Optional[str] = None
    client_request_id: Optional[str] = None
```

**⚠️ 重要发现**:
- `placeholder_created_at` 从前端的 ISO 字符串**自动转换为 datetime 对象**
- 这是 Pydantic 的自动行为

---

### 3️⃣ message.py 处理阶段

**当前代码** (第190-217行):
```python
# 构建元数据
metadata = TaskMetadata(
    client_task_id=body.client_task_id,
    placeholder_created_at=body.placeholder_created_at,  # datetime 对象
)

# 构建纯业务参数
business_params = {}
if body.params:
    for k, v in body.params.items():
        if k not in {"client_task_id", "placeholder_created_at"}:
            business_params[k] = v

if body.model:
    business_params["model"] = body.model

external_task_id = await handler.start(
    message_id=assistant_message_id,
    conversation_id=conversation_id,
    user_id=user_id,
    content=body.content,
    params=business_params,
    metadata=metadata,
)
```

**传递给 Handler 的数据**:
- `params`: `Dict[str, Any]` - 纯业务参数（model, thinking_effort 等）
- `metadata`: `TaskMetadata` - 元数据对象（client_task_id, placeholder_created_at）

---

### 4️⃣ Handler.start() 阶段

**文件**: `backend/services/handlers/chat_handler.py`

**签名**:
```python
async def start(
    self,
    message_id: str,
    conversation_id: str,
    user_id: str,
    content: List[ContentPart],
    params: Dict[str, Any],
    metadata: TaskMetadata,
) -> str
```

**处理逻辑**:
1. 生成 `task_id = metadata.client_task_id or str(uuid.uuid4())`
2. 调用 `_save_task()` 保存任务到数据库
3. 启动异步任务 `_stream_generate()`
4. 返回 `task_id`

---

### 5️⃣ _save_task() 阶段

**当前代码**:
```python
async def _save_task(
    self,
    task_id: str,
    message_id: str,
    conversation_id: str,
    user_id: str,
    model_id: str,
    content: List[ContentPart],
    params: Dict[str, Any],
    metadata: TaskMetadata,
) -> None:
    # 1. 序列化业务参数
    request_params = {
        "content": self._extract_text_content(content),
        "model_id": model_id,
        **self._serialize_params(params),
    }

    # 2. 构建标准 task_data
    task_data = self._build_task_data(
        task_id=task_id,
        message_id=message_id,
        conversation_id=conversation_id,
        user_id=user_id,
        task_type="chat",
        status="running",
        model_id=model_id,
        request_params=request_params,
        metadata=metadata,
    )

    # 3. 保存到数据库
    self.db.table("tasks").insert(task_data).execute()
```

**保存到数据库的 task_data**:
```json
{
  "external_task_id": "xxx-xxx-xxx",
  "conversation_id": "...",
  "user_id": "...",
  "type": "chat",
  "status": "running",
  "model_id": "gemini-2.5-flash",
  "placeholder_message_id": "yyy-yyy-yyy",
  "request_params": {
    "content": "你好",
    "model_id": "gemini-2.5-flash"
  },
  "client_task_id": "xxx-xxx-xxx",
  "placeholder_created_at": "2026-02-10T08:00:00.000000"  // ISO 字符串
}
```

---

### 6️⃣ _stream_generate() 阶段

**流程**:
1. 推送 `message_start` 事件
2. 流式生成内容，推送 `message_chunk` 事件
3. 生成完成，调用 `on_complete()`

---

### 7️⃣ on_complete() 阶段 ⚠️ **关键问题区域**

**当前代码**:
```python
async def on_complete(
    self,
    task_id: str,
    result: List[ContentPart],
    credits_consumed: int = 0,
) -> Message:
    task = await self._get_task(task_id)
    message_id = task["placeholder_message_id"]
    conversation_id = task["conversation_id"]
    client_task_id = task.get("client_task_id") or task_id

    # 1. 扣除积分
    # ...

    # 2. 转换 ContentPart 为字典
    content_dicts = []
    for part in result:
        if isinstance(part, TextPart):
            content_dicts.append({"type": "text", "text": part.text})
        elif isinstance(part, dict):
            content_dicts.append(part)

    # 3. 创建新消息（upsert）
    message_data = {
        "id": message_id,
        "conversation_id": conversation_id,
        "role": MessageRole.ASSISTANT.value,  # ⚠️ 需要导入 MessageRole
        "content": content_dicts,
        "status": MessageStatus.COMPLETED.value,
        "credits_cost": credits_consumed,
        "task_id": client_task_id,
        "generation_params": {"type": "chat", "model": model_id},
    }

    upsert_result = self.db.table("messages").upsert(message_data, on_conflict="id").execute()
    msg_data = upsert_result.data[0]

    # 4. 推送完成消息
    done_msg = build_message_done(
        task_id=client_task_id,
        conversation_id=conversation_id,
        message=msg_data,  # ⚠️ 应该是字典
        credits_consumed=credits_consumed,
    )
    await ws_manager.send_to_task_subscribers(client_task_id, done_msg)

    return message
```

**⚠️ 问题点**:
1. ~~缺少 `MessageRole` 导入~~ ✅ 已修复
2. ~~`build_message_done` 传递 Pydantic 对象而不是字典~~ ✅ 已修复
3. **❓ upsert 是否成功？msg_data 的内容是什么？**

---

### 8️⃣ WebSocket 推送阶段

**文件**: `backend/schemas/websocket.py` - `build_message_done()`

**函数签名**:
```python
def build_message_done(
    task_id: str,
    conversation_id: str,
    message: Dict[str, Any],  # ⚠️ 期望字典
    credits_consumed: Optional[int] = None,
) -> Dict[str, Any]:
    payload = {"message": message}
    if credits_consumed is not None:
        payload["credits_consumed"] = credits_consumed
    return _build_ws_message(
        WSMessageType.MESSAGE_DONE,
        payload,
        task_id=task_id,
        conversation_id=conversation_id,
        message_id=message.get("id"),
    )
```

**推送的 WebSocket 消息格式**:
```json
{
  "type": "message_done",
  "payload": {
    "message": {
      "id": "yyy-yyy-yyy",
      "conversation_id": "...",
      "role": "assistant",
      "content": [{"type": "text", "text": "你好"}],
      "status": "completed",
      "credits_cost": 0,
      "task_id": "xxx-xxx-xxx",
      "generation_params": {"type": "chat", "model": "gemini-2.5-flash"},
      "created_at": "2026-02-10T08:00:00.000000Z"
    },
    "credits_consumed": 0
  },
  "timestamp": 1707552000000,
  "task_id": "xxx-xxx-xxx",
  "conversation_id": "...",
  "message_id": "yyy-yyy-yyy"
}
```

---

### 9️⃣ 前端接收阶段

**文件**: `frontend/src/contexts/WebSocketContext.tsx`

**message_done 处理逻辑** (第223-269行):
```typescript
message_done: (msg) => {
  const { task_id, message_id, conversation_id } = msg;
  const messageData = msg.message || msg.payload?.message;

  // 1. 有 task_id：处理任务完成
  if (task_id) {
    if (messageData && conversation_id) {
      handleTaskDoneWithMessage(task_id, messageData, conversation_id);
    } else if (message_id) {
      store.setStatus(message_id, 'completed');
      store.completeTask(task_id);
    }
    cleanupTaskSubscription(task_id);
  }
  // 2. 无 task_id 但有 messageData
  else if (messageData) {
    const normalized = normalizeMessage(messageData);
    store.updateMessage(message_id || messageData.id, { ...normalized, status: 'completed' });
  }
  // 3. 只有 message_id
  else if (message_id) {
    store.setStatus(message_id, 'completed');
  }

  // 完成流式状态
  if (conversation_id) {
    store.completeStreaming(conversation_id);
    store.setIsSending(false);
  }
}
```

**⚠️ 关键问题**:
- 需要确认 `messageData` 是否正确提取
- 需要确认 `handleTaskDoneWithMessage` 是否正确更新消息状态

---

## 🎯 下一步计划

1. **检查 upsert 返回的 msg_data 内容**
2. **检查 WebSocket 实际推送的数据**
3. **检查前端实际接收到的数据**
4. **验证 handleTaskDoneWithMessage 的逻辑**

---

## 🐛 已发现的问题

| 问题 | 状态 | 修复 |
|-----|------|------|
| datetime 序列化失败 | ✅ 已修复 | 添加 `_serialize_params()` |
| 缺少 MessageRole 导入 | ✅ 已修复 | 添加导入 |
| build_message_done 传递错误类型 | ✅ 已修复 | 使用 `msg_data` |
| 前端状态未更新 | ❌ 待排查 | 需要检查 WebSocket 数据 |

---

## 🔄 前端消息发送调用链

### 完整调用链

```
用户点击发送按钮或按 Enter
  ↓
InputArea.handleSubmit() (InputArea.tsx:191)
  ├─ 检查任务限制：useMessageStore.canStartTask()
  ├─ 准备数据：messageContent, uploadedImageUrls
  ├─ 新对话：createConversation() 获取 conversationId
  ↓
根据 selectedModel.type 路由到不同 handler
  ├─ type === 'chat': handleChatMessage()
  ├─ type === 'video': handleVideoGeneration()
  └─ type === 'image': handleImageGeneration()
  ↓
useTextMessageHandler.handleChatMessage() (useTextMessageHandler.ts:32)
  ├─ 构建 content: createTextContent() / createTextWithImage()
  ├─ 广播事件：tabSync.broadcast('chat_started')
  ↓
  调用统一发送器 sendMessage() (messageSender.ts:88)
    ├─ Phase 1: 乐观更新（创建用户消息 + 占位符）
    ├─ Phase 1.5: 提前订阅 WebSocket (subscribeTaskWithMapping)
    ├─ Phase 2: 调用后端 API ⚠️ 关键点
    │   ↓
    │   request<GenerateResponse>({
    │     url: `/conversations/${conversationId}/messages/generate`,
    │     method: 'POST',
    │     data: {
    │       operation, content, generation_type, model,
    │       params, client_task_id, assistant_message_id,
    │       placeholder_created_at, ...
    │     }
    │   })
    ├─ Phase 3: 更新消息状态（task_id）
    ├─ Phase 4: 创建任务追踪
    └─ Phase 5: 验证 task_id 一致性
```

### ⚠️ 当前问题

**现象**：后端日志显示没有收到 POST `/api/conversations/.../messages/generate` 请求

**可能原因**：
1. ❓ `sendMessage()` 在 Phase 2 之前就失败了（JavaScript 错误）
2. ❓ `request()` 函数实现有问题
3. ❓ 浏览器 Console 有错误信息被忽略

**下一步**：检查浏览器 Console 是否有 JavaScript 错误

---

## 📝 待补充内容

- [ ] `handleTaskDoneWithMessage` 的实现
- [ ] 前端 `normalizeMessage` 的逻辑
- [ ] 数据库 messages 表的实际结构
- [ ] WebSocket 实际传输的数据示例
- [ ] `request()` 函数的实现细节
