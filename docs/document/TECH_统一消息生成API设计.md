# 统一消息生成 API 设计方案

> **版本**: v1.0
> **日期**: 2026-02-07
> **状态**: 设计中

## 1. 背景与问题

### 1.1 当前架构问题

| 操作 | 当前实现 | 问题 |
|------|---------|------|
| `send` | `POST /messages/send` (WebSocket) | 正常工作 |
| `retry` | `POST /messages/{id}/regenerate` (SSE) | 使用旧 SSE，与新架构不一致 |
| `regenerate` | 前端调用 `/messages/send` | 创建新用户消息，但后端无法区分 |

### 1.2 前端期望行为

| 操作 | 用户消息 | AI 消息 | 说明 |
|------|---------|---------|------|
| `send` | 创建新 | 创建新 | 正常发送 |
| `retry` | 不创建 | 原地更新 | 错误消息重试 |
| `regenerate` | 创建新 | 创建新 | 成功消息重新生成 |

### 1.3 核心矛盾

- **后端 `regenerate_message_stream`**: 原地更新失败消息（对应前端 `retry`）
- **前端 `regenerate`**: 期望创建新消息对（目前复用 `send` 接口）
- **图片/视频 `retry`**: 前端 `backendAPI.ts` 已修复跳过用户消息创建

## 2. 设计目标

1. **统一入口**: 单一 API 处理 send/retry/regenerate
2. **WebSocket 推送**: 完全替代 SSE，与新架构一致
3. **语义清晰**: 操作类型决定消息创建/更新行为
4. **向后兼容**: 保留旧接口过渡期

## 3. API 设计

### 3.1 统一端点

```
POST /conversations/{conversation_id}/messages/generate
```

### 3.2 请求体

```typescript
interface GenerateMessageRequest {
  // 操作类型
  operation: 'send' | 'retry' | 'regenerate';

  // 内容（send/regenerate 必填，retry 可选覆盖）
  content?: string;

  // 原消息 ID（retry/regenerate 必填）
  original_message_id?: string;

  // 模型配置
  model_id?: string;

  // VQA 附件
  image_url?: string;
  video_url?: string;

  // 推理参数（Gemini 3 专用）
  thinking_effort?: 'minimal' | 'low' | 'medium' | 'high';
  thinking_mode?: 'default' | 'deep_think';

  // 前端预分配 ID（用于乐观更新）
  client_request_id?: string;
  created_at?: string;  // ISO 8601
  assistant_message_id?: string;
}
```

### 3.3 响应体

```typescript
interface GenerateMessageResponse {
  // 任务信息
  task_id: string;

  // 用户消息（send/regenerate 返回，retry 为 null）
  user_message: Message | null;

  // AI 消息 ID（用于占位符绑定）
  assistant_message_id: string;

  // 操作类型（回显）
  operation: 'send' | 'retry' | 'regenerate';
}
```

### 3.4 操作行为矩阵

| 操作 | `original_message_id` | `content` | 用户消息 | AI 消息 |
|------|----------------------|-----------|---------|---------|
| `send` | 不需要 | 必填 | 创建新 | 创建新 |
| `retry` | 必填 | 可选（覆盖原内容） | 不创建 | 更新原消息 |
| `regenerate` | 必填 | 可选（默认复用原用户消息） | 创建新 | 创建新 |

## 4. 后端实现

### 4.1 新增 Schema

```python
# schemas/message.py

class MessageOperation(str, Enum):
    """消息操作类型"""
    SEND = "send"
    RETRY = "retry"
    REGENERATE = "regenerate"


class GenerateMessageRequest(BaseModel):
    """统一消息生成请求"""
    operation: MessageOperation = MessageOperation.SEND
    content: Optional[str] = Field(None, max_length=10000)
    original_message_id: Optional[str] = Field(None, max_length=100)

    model_id: Optional[str] = None
    image_url: Optional[str] = None
    video_url: Optional[str] = None
    thinking_effort: Optional[str] = None
    thinking_mode: Optional[str] = None

    client_request_id: Optional[str] = Field(None, max_length=100)
    created_at: Optional[datetime] = None
    assistant_message_id: Optional[str] = Field(None, max_length=100)

    @model_validator(mode='after')
    def validate_operation_params(self) -> Self:
        """验证操作参数完整性"""
        if self.operation == MessageOperation.SEND:
            if not self.content:
                raise ValueError('send 操作必须提供 content')
        elif self.operation in (MessageOperation.RETRY, MessageOperation.REGENERATE):
            if not self.original_message_id:
                raise ValueError(f'{self.operation.value} 操作必须提供 original_message_id')
        return self


class GenerateMessageResponse(BaseModel):
    """统一消息生成响应"""
    task_id: str
    user_message: Optional[MessageResponse] = None
    assistant_message_id: str
    operation: MessageOperation
```

### 4.2 路由实现

```python
# api/routes/message.py

@router.post("/generate", response_model=GenerateMessageResponse, summary="统一消息生成")
@limiter.limit(RATE_LIMITS["message_stream"])
async def generate_message(
    request: Request,
    conversation_id: str,
    body: GenerateMessageRequest,
    current_user: CurrentUser,
    task_limit_service: TaskLimitSvc,
    service: MessageStreamService = Depends(get_message_stream_service),
):
    """
    统一消息生成入口（WebSocket 推送）

    支持三种操作：
    - send: 发送新消息
    - retry: 重试失败的 AI 消息（原地更新）
    - regenerate: 重新生成成功的 AI 消息（创建新消息对）

    WebSocket 事件：
    - chat_start: AI 开始生成
    - chat_chunk: 流式内容块
    - chat_done: 生成完成
    - chat_error: 发生错误
    """
    if task_limit_service:
        await task_limit_service.check_and_acquire(current_user["id"], conversation_id)

    result = await service.generate_message(
        conversation_id=conversation_id,
        user_id=current_user["id"],
        operation=body.operation,
        content=body.content,
        original_message_id=body.original_message_id,
        model_id=body.model_id,
        image_url=body.image_url,
        video_url=body.video_url,
        thinking_effort=body.thinking_effort,
        thinking_mode=body.thinking_mode,
        client_request_id=body.client_request_id,
        created_at=body.created_at,
        assistant_message_id=body.assistant_message_id,
    )
    return result
```

### 4.3 Service 实现

```python
# services/message_stream_service.py

async def generate_message(
    self,
    conversation_id: str,
    user_id: str,
    operation: MessageOperation,
    content: Optional[str] = None,
    original_message_id: Optional[str] = None,
    model_id: Optional[str] = None,
    image_url: Optional[str] = None,
    video_url: Optional[str] = None,
    thinking_effort: Optional[str] = None,
    thinking_mode: Optional[str] = None,
    client_request_id: Optional[str] = None,
    created_at: Optional[datetime] = None,
    assistant_message_id: Optional[str] = None,
) -> dict:
    """
    统一消息生成处理

    根据 operation 类型执行不同逻辑：
    - send: 创建用户消息 + 创建新 AI 消息
    - retry: 不创建用户消息 + 原地更新 AI 消息
    - regenerate: 创建用户消息 + 创建新 AI 消息
    """
    user_message = None
    final_content = content

    # ========== 1. 处理原消息（retry/regenerate） ==========
    if operation in (MessageOperation.RETRY, MessageOperation.REGENERATE):
        original_msg = await self._get_and_validate_message(
            original_message_id, conversation_id, user_id
        )

        # 获取上下文用户消息
        context_user_msg = await self._get_context_user_message(
            conversation_id, original_msg
        )

        if not final_content:
            final_content = context_user_msg["content"]
            image_url = image_url or context_user_msg.get("image_url")

    # ========== 2. 创建用户消息（send/regenerate） ==========
    if operation in (MessageOperation.SEND, MessageOperation.REGENERATE):
        user_message = await self.message_service.create_message(
            conversation_id, user_id, final_content, "user", 0,
            image_url, video_url,
            client_request_id=client_request_id,
            created_at=created_at,
        )

        # 更新对话标题
        await self.message_service._update_conversation_title_if_first_message(
            conversation_id, user_id, final_content
        )

    # ========== 3. 准备 AI 任务 ==========
    task_id = str(uuid.uuid4())

    # retry 复用原消息 ID，其他情况使用预分配或新生成
    if operation == MessageOperation.RETRY:
        assistant_message_id = original_message_id
    else:
        assistant_message_id = assistant_message_id or str(uuid.uuid4())

    # ========== 4. 创建任务记录 ==========
    self.db.table("tasks").insert({
        "id": task_id,
        "external_task_id": task_id,
        "user_id": user_id,
        "conversation_id": conversation_id,
        "type": "chat",
        "status": "pending",
        "request_params": {
            "operation": operation.value,
            "content": final_content,
            "image_url": image_url,
            "video_url": video_url,
            "thinking_effort": thinking_effort,
            "thinking_mode": thinking_mode,
            "original_message_id": original_message_id,
        },
        "model_id": model_id,
        "assistant_message_id": assistant_message_id,
        "credits_locked": 0,
        "started_at": datetime.now(timezone.utc).isoformat(),
    }).execute()

    # ========== 5. 启动后台处理 ==========
    model, client, adapter = prepare_ai_stream_client(model_id)

    try:
        stream = await stream_ai_response(
            adapter=adapter,
            get_conversation_history_func=self.message_service._get_conversation_history,
            conversation_id=conversation_id,
            user_id=user_id,
            content=final_content,
            image_url=image_url,
            video_url=video_url,
            thinking_effort=thinking_effort,
            thinking_mode=thinking_mode,
        )

        asyncio.create_task(
            self._process_generate_stream(
                task_id=task_id,
                conversation_id=conversation_id,
                user_id=user_id,
                operation=operation,
                assistant_message_id=assistant_message_id,
                stream=stream,
                model=model,
                adapter=adapter,
                client=client,
            )
        )
    except Exception as e:
        await client.close()
        self.db.table("tasks").update({
            "status": "failed",
            "error_message": str(e),
            "completed_at": datetime.now(timezone.utc).isoformat(),
        }).eq("id", task_id).execute()
        raise

    return {
        "task_id": task_id,
        "user_message": user_message,
        "assistant_message_id": assistant_message_id,
        "operation": operation,
    }
```

### 4.4 后台流处理（区分 retry/其他）

```python
async def _process_generate_stream(
    self,
    task_id: str,
    conversation_id: str,
    user_id: str,
    operation: MessageOperation,
    assistant_message_id: str,
    stream,
    model: str,
    adapter,
    client,
):
    """统一后台流处理 - 根据 operation 决定创建/更新行为"""
    from services.websocket_manager import ws_manager
    from schemas.websocket import (
        build_chat_start_message,
        build_chat_chunk_message,
        build_chat_done_message,
        build_chat_error_message,
    )

    full_content = ""
    total_credits = 0

    try:
        # ... 流处理逻辑 ...

        # ========== 完成处理 ==========
        if full_content:
            if operation == MessageOperation.RETRY:
                # RETRY: 原地更新消息
                self.db.table("messages").update({
                    "content": full_content,
                    "is_error": False,
                    "credits_cost": total_credits,
                }).eq("id", assistant_message_id).execute()

                # 获取更新后的完整消息
                msg_result = self.db.table("messages")\
                    .select("*")\
                    .eq("id", assistant_message_id)\
                    .single().execute()
                assistant_message = format_message(msg_result.data)
            else:
                # SEND/REGENERATE: 创建新消息
                assistant_message = await self.message_service.create_message(
                    conversation_id=conversation_id,
                    user_id=user_id,
                    content=full_content,
                    role="assistant",
                    credits_cost=total_credits,
                    message_id=assistant_message_id,
                )

            # 扣积分
            await deduct_user_credits(
                self.db, user_id, total_credits, f"AI 对话 ({model})"
            )

            # 广播完成
            await ws_manager.send_to_task_subscribers(
                task_id,
                build_chat_done_message(
                    task_id=task_id,
                    conversation_id=conversation_id,
                    message_id=assistant_message_id,
                    content=full_content,
                    credits_consumed=total_credits,
                    model=model,
                ),
                buffer=False,
            )

    except Exception as e:
        # 错误处理 - RETRY 更新原消息，其他创建错误消息
        if operation == MessageOperation.RETRY:
            self.db.table("messages").update({
                "content": "重试失败，AI 服务暂时不可用。",
                "is_error": True,
            }).eq("id", assistant_message_id).execute()
        else:
            await self.message_service.create_error_message(
                conversation_id=conversation_id,
                user_id=user_id,
                content="抱歉，AI 服务暂时不可用，请稍后重试。",
                message_id=assistant_message_id,
            )
        # ... 广播错误 ...

    finally:
        await client.close()
```

## 5. 前端适配

### 5.1 API 层修改

```typescript
// services/message.ts

export interface GenerateMessageParams {
  operation: 'send' | 'retry' | 'regenerate';
  content?: string;
  original_message_id?: string;
  model_id?: string;
  image_url?: string;
  thinking_effort?: string;
  thinking_mode?: string;
  client_request_id?: string;
  created_at?: string;
  assistant_message_id?: string;
}

export interface GenerateMessageResponse {
  task_id: string;
  user_message: Message | null;
  assistant_message_id: string;
  operation: 'send' | 'retry' | 'regenerate';
}

export async function generateMessage(
  conversationId: string,
  params: GenerateMessageParams
): Promise<GenerateMessageResponse> {
  const response = await api.post(
    `/conversations/${conversationId}/messages/generate`,
    params
  );
  return response.data;
}
```

### 5.2 统一发送器适配

```typescript
// services/messageSender/chatSender.ts

async function callChatAPI(
  params: UnifiedMessageParams,
  lifecycle: MessageLifecycle
): Promise<UnifiedAPIResponse> {
  const { operation, conversationId, content, originalMessage } = params;

  const response = await generateMessage(conversationId, {
    operation,
    content,
    original_message_id: operation !== 'send' ? originalMessage?.id : undefined,
    model_id: params.modelId,
    image_url: params.imageUrl,
    thinking_effort: params.chatParams?.thinkingEffort,
    thinking_mode: params.chatParams?.deepThinkMode ? 'deep_think' : 'default',
    client_request_id: lifecycle.clientRequestId,
    created_at: lifecycle.timestamps.user,
    assistant_message_id: lifecycle.tempTaskId,
  });

  return {
    taskId: response.task_id,
    userMessage: response.user_message,
    assistantMessageId: response.assistant_message_id,
  };
}
```

## 6. 迁移计划

### Phase 1: 后端实现（保留旧接口）
1. 新增 `GenerateMessageRequest` / `GenerateMessageResponse` Schema
2. 新增 `/messages/generate` 端点
3. 实现 `generate_message` service 方法
4. 测试三种操作模式

### Phase 2: 前端迁移
1. 新增 `generateMessage` API 函数
2. 修改 `chatSender.ts` 使用新接口
3. 验证 send/retry/regenerate 流程
4. 清理旧 `sendMessage` 调用

### Phase 3: 清理旧接口
1. 标记 `/messages/send` 为 deprecated
2. 删除 `/messages/{id}/regenerate` (SSE)
3. 清理 `MessageStreamService.regenerate_message_stream`

## 7. WebSocket 事件设计

### 7.1 事件类型（无变化）

| 事件 | 说明 |
|------|------|
| `chat_start` | AI 开始生成 |
| `chat_chunk` | 流式内容块 |
| `chat_done` | 生成完成 |
| `chat_error` | 发生错误 |

### 7.2 `chat_done` 事件扩展

```typescript
interface ChatDoneEvent {
  type: 'chat_done';
  data: {
    task_id: string;
    conversation_id: string;
    message_id: string;
    content: string;
    credits_consumed: number;
    model: string;
    // 新增：操作类型回显
    operation?: 'send' | 'retry' | 'regenerate';
  };
}
```

## 8. 边界情况分析

### 8.1 消息状态校验

| 场景 | 问题 | 解决方案 |
|------|------|---------|
| `retry` 非错误消息 | 用户可能误操作 | 后端校验 `is_error=true`，否则返回 400 |
| `regenerate` 错误消息 | 语义不清 | 后端校验 `is_error=false`，否则返回 400 |
| 原消息被删除 | 找不到消息 | 返回 404，前端清理占位符 |
| 原消息不属于该对话 | 安全问题 | 返回 403 |

```python
# 校验逻辑
if operation == MessageOperation.RETRY:
    if not original_msg.get("is_error"):
        raise HTTPException(400, "retry 只能用于错误消息")
elif operation == MessageOperation.REGENERATE:
    if original_msg.get("is_error"):
        raise HTTPException(400, "regenerate 只能用于成功消息，错误消息请用 retry")
```

### 8.2 并发操作

| 场景 | 问题 | 解决方案 |
|------|------|---------|
| 连续点击 retry | 重复任务 | 前端禁用按钮 + 后端幂等检查 |
| retry 进行中再次 retry | 任务冲突 | 检查是否有 pending/running 任务 |
| 多标签页同时操作 | 状态不同步 | WebSocket 广播 + 乐观锁 |

```python
# 后端检查进行中的任务
existing_task = self.db.table("tasks")\
    .select("id")\
    .eq("assistant_message_id", original_message_id)\
    .in_("status", ["pending", "running"])\
    .single().execute()

if existing_task.data:
    raise HTTPException(409, "该消息正在处理中，请稍候")
```

### 8.3 用户上下文恢复

| 场景 | 问题 | 解决方案 |
|------|------|---------|
| 找不到上下文用户消息 | 历史被清理 | 返回 400，提示"无法恢复上下文" |
| 用户消息内容为空 | 异常数据 | 返回 400 |
| 用户消息含附件 | retry 是否继承 | 默认继承，可通过参数覆盖 |

```python
async def _get_context_user_message(self, conversation_id: str, ai_message: dict) -> dict:
    """获取 AI 消息之前的用户消息"""
    result = self.db.table("messages")\
        .select("*")\
        .eq("conversation_id", conversation_id)\
        .eq("role", "user")\
        .lt("created_at", ai_message["created_at"])\
        .order("created_at", desc=True)\
        .limit(1)\
        .single().execute()

    if not result.data:
        raise HTTPException(400, "无法找到原始用户消息，请重新发送")

    return result.data
```

### 8.4 积分处理

| 场景 | 当前处理 | 建议 |
|------|---------|------|
| `send` 成功 | 扣积分 | 保持不变 |
| `send` 失败 | 不扣积分 | 保持不变 |
| `retry` 成功 | 扣积分 | **再次扣积分**（新的 API 调用） |
| `retry` 失败 | 不扣积分 | 保持不变 |
| `regenerate` 成功 | 扣积分 | 正常扣积分 |

**说明**：`retry` 不退还原积分，因为原请求可能已消耗 API 资源。

### 8.5 模型变更

| 场景 | 允许 | 说明 |
|------|------|------|
| retry 换模型 | ✅ 允许 | 用户可能想换个模型试试 |
| retry 不传模型 | 使用对话默认 | 从 conversation.model_id 获取 |
| 模型已下线 | 返回 400 | "该模型暂不可用" |

### 8.6 历史上下文

| 场景 | 处理方式 |
|------|---------|
| `retry` 历史上下文 | 使用**当前**历史（可能包含新消息） |
| `regenerate` 历史上下文 | 使用**当前**历史 |

**风险**：如果用户在错误后又发了新消息，retry 的上下文会包含这些新消息。

**建议**：可选参数 `use_original_context: bool`，默认 `false`。

### 8.7 前端占位符处理

| 操作 | 占位符 ID | RuntimeStore 处理 |
|------|---------|-------------------|
| `send` | `streaming-{new_task_id}` | 新建占位符 |
| `retry` | 复用 `original_message_id` | **不创建占位符**，直接更新原消息 |
| `regenerate` | `streaming-{new_task_id}` | 新建占位符 |

```typescript
// 前端 retry 处理
if (operation === 'retry') {
  // 不创建占位符，而是清空原消息内容
  runtimeStore.updateMessage(conversationId, originalMessageId, {
    content: '',
    is_error: false,
  });
}
```

### 8.8 WebSocket 断线恢复

| 场景 | 处理 |
|------|------|
| retry 进行中断线 | 重连后订阅任务，恢复流式内容 |
| 页面刷新 | 从 tasks 表恢复，更新原消息 |

**注意**：`retry` 任务的 `assistant_message_id` 就是原消息 ID，恢复时直接更新。

### 8.9 图片/视频任务

当前设计仅针对 **chat 任务**。图片/视频任务保持现有逻辑：
- 图片/视频没有"流式"概念，不需要 retry
- 失败后重新生成即可（用户重新点击生成按钮）

**可选扩展**：如果需要支持图片/视频 retry，可以复用此 API，但需要调整后端逻辑。

### 8.10 幂等性

| 字段 | 作用 |
|------|------|
| `client_request_id` | 防止重复提交 |
| `assistant_message_id` | 前端预分配，确保占位符绑定 |

```python
# 幂等检查（可选）
existing = self.db.table("tasks")\
    .select("id")\
    .eq("client_request_id", client_request_id)\
    .single().execute()

if existing.data:
    # 返回已存在的任务，而非创建新任务
    return existing.data
```

## 9. 验收标准

### 功能验收
- [ ] `send` 操作：创建用户消息 + 创建 AI 消息
- [ ] `retry` 操作：不创建用户消息 + 原地更新 AI 消息
- [ ] `regenerate` 操作：创建用户消息 + 创建 AI 消息
- [ ] 所有操作通过 WebSocket 推送流式内容
- [ ] 错误处理符合操作语义

### 边界验收
- [ ] `retry` 非错误消息返回 400
- [ ] `regenerate` 错误消息返回 400
- [ ] 原消息不存在返回 404
- [ ] 并发 retry 返回 409
- [ ] 积分正确扣除
- [ ] WebSocket 断线后正确恢复

## 10. 媒体任务架构现状

> **状态**: ✅ 已迁移到新架构

### 10.1 架构确认

媒体任务（图片/视频）已完全迁移到 POST + WebSocket 架构：

| 组件 | 文件 | 使用方式 |
|------|------|---------|
| 统一入口 | `useMediaMessageHandler.ts` | 调用 `sendUnifiedMessage` |
| WebSocket 订阅 | `WebSocketContext.tsx` | `subscribeTaskWithMapping` |
| 任务状态处理 | `WebSocketContext.tsx` | `task_status` 事件 |
| 任务注册 | `unifiedSender.ts:260-274` | `taskStore.startMediaTask()` |

### 10.2 媒体任务流程

```
用户点击生成 → sendUnifiedMessage() → POST /tasks/image|video
                    ↓
             taskStore.startMediaTask() ← 注册任务
                    ↓
             subscribeTaskWithMapping() ← WebSocket 订阅
                    ↓
             task_status 事件 → 更新占位符 → 替换为最终结果
```

### 10.3 无需改动

媒体任务与 Chat API 统一无关，保持独立：
- 图片/视频没有"流式"概念
- 使用 `task_status` 事件而非 `chat_chunk`
- 已完全脱离 SSE/polling

## 11. Phase 3: 旧代码清理计划

### 11.1 清理范围

统一 Chat API 实现完成后，需删除以下旧代码：

#### 后端清理

| 文件 | 内容 | 行号 | 说明 |
|------|------|------|------|
| `api/routes/message.py` | `/messages/{id}/regenerate` 端点 | 232-280 | SSE 流式接口 |
| `api/routes/task.py` | `/tasks/{id}/stream` 端点 | 120-238 | SSE + polling 回退 |
| `services/message_stream_service.py` | `regenerate_message_stream()` 方法 | 542+ | 旧 regenerate 实现 |

#### 前端清理

| 文件 | 内容 | 说明 |
|------|------|------|
| 无需清理 | - | 前端已完全迁移到 WebSocket |

### 11.2 清理步骤

```bash
# Step 1: 删除后端 SSE 端点
# api/routes/message.py - 删除 regenerate_message 函数
# api/routes/task.py - 删除 stream_chat_task 函数

# Step 2: 删除 Service 层旧方法
# services/message_stream_service.py - 删除 regenerate_message_stream 方法

# Step 3: 清理 import
# 删除未使用的 StreamingResponse 等导入

# Step 4: 运行测试确认无依赖
pytest backend/tests/ -v
```

### 11.3 清理检查清单

```markdown
## 后端清理验证
- [ ] `/messages/{id}/regenerate` 端点已删除
- [ ] `/tasks/{id}/stream` 端点已删除
- [ ] `regenerate_message_stream` 方法已删除
- [ ] `StreamingResponse` 导入已清理（如果无其他用途）
- [ ] 无 `text/event-stream` 相关代码
- [ ] 无 polling 循环代码

## 前端清理验证
- [ ] 无 `EventSource` 使用
- [ ] 无 `setInterval` 轮询任务状态
- [ ] 所有消息操作通过 `sendUnifiedMessage`

## 集成测试
- [ ] Chat send 正常工作
- [ ] Chat retry 正常工作
- [ ] Chat regenerate 正常工作
- [ ] 图片生成正常工作
- [ ] 视频生成正常工作
- [ ] WebSocket 断线重连后恢复
```

## 12. 深度清理阶段

### 12.1 目标

在 Phase 3 完成后，进行全面的代码审查和清理，确保：
- 无死代码
- 无未使用的类型定义
- 无冗余的工具函数
- 架构一致性

### 12.2 审查范围

```
深度推理清理检查清单：

1. 类型定义审查
   - schemas/message.py: 清理旧的 SSE 相关类型
   - frontend/src/types/: 清理未使用的任务状态类型

2. 工具函数审查
   - 检查是否有专为 SSE 设计的工具函数
   - 检查是否有 polling 相关的 hooks

3. 常量审查
   - 检查是否有 SSE 相关的常量定义
   - 检查是否有 polling 间隔等配置

4. 依赖审查
   - 检查是否有仅为 SSE 使用的第三方库

5. 测试文件审查
   - 清理 SSE 相关的测试用例
   - 更新测试以覆盖新 WebSocket 流程
```

### 12.3 清理优先级

| 优先级 | 内容 | 影响 |
|--------|------|------|
| P0 | 删除 SSE 端点 | 阻止新代码依赖旧接口 |
| P1 | 删除 Service 层方法 | 减少代码体积 |
| P2 | 清理类型定义 | 提高代码可维护性 |
| P3 | 更新文档 | 保持文档准确性 |

## 13. 附录

### 13.1 版本历史

| 版本 | 日期 | 变更 |
|------|------|------|
| v1.0 | 2026-02-07 | 初始设计 |
| v1.1 | 2026-02-07 | 添加边界情况分析 |
| v1.2 | 2026-02-07 | 添加媒体任务确认 + 旧代码清理计划 |

### 13.2 相关文档

- [TECH_WebSocket实时推送.md](./TECH_WebSocket实时推送.md) - WebSocket 实现细节
- [TECH_统一任务恢复入口.md](./TECH_统一任务恢复入口.md) - 任务恢复设计
