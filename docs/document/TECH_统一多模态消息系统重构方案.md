# 统一多模态消息系统重构方案

> **版本**: v2.0 | **状态**: 待评审 | **日期**: 2026-02-07

## 一、设计理念

### 1.1 参考架构

| 来源 | 核心思想 | 采纳点 |
|------|---------|--------|
| [OpenAI GPT-4o](https://openai.com/index/hello-gpt-4o/) | 统一模型处理多模态 | `content: ContentPart[]` 数组格式 |
| [LobeChat](https://lobehub.com/docs/development/basic/architecture) | 统一 AgentRuntime 路由 | 消息类型路由到不同 Handler |
| [Replicate](https://replicate.com/docs/topics/webhooks) | Webhook 回调 | 长时任务完成通知 |
| [LangChain](https://python.langchain.com/docs/how_to/multimodal_inputs/) | 统一 Pipeline | 处理管道标准化 |

### 1.2 核心原则

1. **一个消息模型**: 所有类型（text/image/video/audio）使用相同数据结构
2. **一个 API 入口**: `/messages/generate` 处理所有生成请求
3. **一个 Store**: 统一管理消息和任务状态
4. **一套 WebSocket 协议**: 统一的消息推送格式

---

## 二、统一消息模型

### 2.1 数据结构设计

```typescript
// ============================================================
// 内容部件（OpenAI 风格）
// ============================================================

type ContentPart =
  | TextPart
  | ImagePart
  | VideoPart
  | AudioPart
  | FilePart;

interface TextPart {
  type: 'text';
  text: string;
}

interface ImagePart {
  type: 'image';
  url: string;           // OSS URL
  width?: number;
  height?: number;
  alt?: string;          // 无障碍描述
}

interface VideoPart {
  type: 'video';
  url: string;
  duration?: number;     // 秒
  thumbnail?: string;    // 封面图
}

interface AudioPart {
  type: 'audio';
  url: string;
  duration?: number;
  transcript?: string;   // 语音转文字
}

interface FilePart {
  type: 'file';
  url: string;
  name: string;
  mime_type: string;
  size?: number;         // 字节
}

// ============================================================
// 统一消息模型
// ============================================================

interface Message {
  id: string;
  conversation_id: string;
  role: 'user' | 'assistant' | 'system';

  // 核心：统一的内容数组
  content: ContentPart[];

  // 状态
  status: 'pending' | 'streaming' | 'completed' | 'failed';

  // 生成相关
  task_id?: string;              // 关联的任务 ID
  generation_params?: {
    model: string;
    [key: string]: unknown;
  };

  // 计费
  credits_cost?: number;

  // 错误信息
  error?: {
    code: string;
    message: string;
  };

  // 时间戳
  created_at: string;
  updated_at?: string;
}
```

### 2.2 数据库 Schema

```sql
-- 消息表（重构后）
CREATE TABLE messages (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  conversation_id UUID NOT NULL REFERENCES conversations(id),
  role VARCHAR(20) NOT NULL,

  -- 核心变更：content 改为 JSONB 数组
  content JSONB NOT NULL DEFAULT '[]',

  -- 状态
  status VARCHAR(20) NOT NULL DEFAULT 'completed',
  task_id VARCHAR(100),

  -- 生成参数
  generation_params JSONB,

  -- 计费
  credits_cost INTEGER DEFAULT 0,

  -- 错误
  error JSONB,

  -- 时间戳
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ,

  -- 索引
  CONSTRAINT fk_conversation FOREIGN KEY (conversation_id)
    REFERENCES conversations(id) ON DELETE CASCADE
);

CREATE INDEX idx_messages_conversation ON messages(conversation_id);
CREATE INDEX idx_messages_task ON messages(task_id);
CREATE INDEX idx_messages_status ON messages(status);
```

### 2.3 数据迁移

```sql
-- 迁移脚本：将旧格式转换为新格式
UPDATE messages SET content =
  CASE
    -- 纯文本消息
    WHEN image_url IS NULL AND video_url IS NULL THEN
      jsonb_build_array(jsonb_build_object('type', 'text', 'text', COALESCE(content_text, '')))

    -- 图片消息
    WHEN image_url IS NOT NULL AND video_url IS NULL THEN
      jsonb_build_array(
        jsonb_build_object('type', 'text', 'text', COALESCE(content_text, '')),
        jsonb_build_object('type', 'image', 'url', image_url)
      )

    -- 视频消息
    WHEN video_url IS NOT NULL THEN
      jsonb_build_array(
        jsonb_build_object('type', 'text', 'text', COALESCE(content_text, '')),
        jsonb_build_object('type', 'video', 'url', video_url)
      )

    ELSE jsonb_build_array()
  END
WHERE content = '[]' OR content IS NULL;

-- 迁移完成后删除旧字段（可选，建议保留一段时间）
-- ALTER TABLE messages DROP COLUMN content_text;
-- ALTER TABLE messages DROP COLUMN image_url;
-- ALTER TABLE messages DROP COLUMN video_url;
```

---

## 三、统一 API 设计

### 3.1 API 端点

```
POST /api/v1/conversations/{conversation_id}/messages/generate
```

### 3.2 请求格式

```typescript
interface GenerateRequest {
  // 操作类型
  operation: 'send' | 'regenerate' | 'retry';

  // 用户输入内容（统一格式）
  content: ContentPart[];

  // 生成类型（自动推断或显式指定）
  generation_type?: 'chat' | 'image' | 'video' | 'audio';

  // 模型配置
  model?: string;

  // 类型特定参数
  params?: {
    // Chat
    thinking_effort?: 'minimal' | 'low' | 'medium' | 'high';

    // Image
    aspect_ratio?: '1:1' | '16:9' | '9:16';
    resolution?: '1K' | '2K' | '4K';

    // Video
    duration?: number;

    // 通用
    [key: string]: unknown;
  };

  // 重新生成时的原消息 ID
  original_message_id?: string;

  // 幂等性
  client_request_id?: string;
}
```

### 3.3 响应格式

```typescript
interface GenerateResponse {
  // 任务 ID（所有类型都有）
  task_id: string;

  // 用户消息（send/regenerate 操作）
  user_message?: Message;

  // 助手消息占位符
  assistant_message: Message;  // status = 'pending' | 'streaming'

  // 预估完成时间（毫秒）
  estimated_time_ms?: number;
}
```

### 3.4 后端实现

```python
# backend/api/routes/message.py

@router.post("/conversations/{conversation_id}/messages/generate")
async def generate_message(
    conversation_id: str,
    body: GenerateRequest,
    current_user: User = Depends(get_current_user),
) -> GenerateResponse:
    """
    统一消息生成入口

    根据 generation_type 或 content 自动路由到对应 Handler
    """
    # 1. 推断生成类型
    gen_type = body.generation_type or infer_generation_type(body.content)

    # 2. 创建用户消息（send/regenerate）
    user_message = None
    if body.operation != 'retry':
        user_message = await message_service.create_message(
            conversation_id=conversation_id,
            role='user',
            content=body.content,
            status='completed',
        )

    # 3. 创建助手消息占位符
    assistant_message = await message_service.create_message(
        conversation_id=conversation_id,
        role='assistant',
        content=[],  # 空内容，待填充
        status='pending',
        generation_params={
            'type': gen_type,
            'model': body.model,
            **body.params,
        },
    )

    # 4. 路由到对应 Handler
    handler = get_handler(gen_type)  # ChatHandler / ImageHandler / VideoHandler
    task_id = await handler.start(
        message_id=assistant_message.id,
        conversation_id=conversation_id,
        user_id=current_user.id,
        content=body.content,
        params=body.params,
    )

    # 5. 更新消息的 task_id
    await message_service.update_message(
        message_id=assistant_message.id,
        task_id=task_id,
    )

    return GenerateResponse(
        task_id=task_id,
        user_message=user_message,
        assistant_message=assistant_message,
    )


def infer_generation_type(content: List[ContentPart]) -> str:
    """根据内容推断生成类型"""
    text_parts = [p for p in content if p['type'] == 'text']
    image_parts = [p for p in content if p['type'] == 'image']

    if not text_parts:
        return 'chat'

    text = text_parts[0]['text'].lower()

    # 简单的关键词匹配（可以用更智能的方式）
    if any(kw in text for kw in ['生成图片', '画一', 'generate image', '/image']):
        return 'image'
    if any(kw in text for kw in ['生成视频', '做个视频', 'generate video', '/video']):
        return 'video'

    # 如果包含图片输入，可能是图生图或图生视频
    if image_parts:
        if any(kw in text for kw in ['变成视频', 'to video']):
            return 'video'
        if any(kw in text for kw in ['修改', '编辑', 'edit']):
            return 'image'

    return 'chat'
```

---

## 四、统一 Handler 架构

### 4.1 Handler 基类

```python
# backend/services/handlers/base.py

from abc import ABC, abstractmethod
from typing import List, Dict, Any

class BaseHandler(ABC):
    """统一的消息处理器基类"""

    @abstractmethod
    async def start(
        self,
        message_id: str,
        conversation_id: str,
        user_id: str,
        content: List[Dict[str, Any]],
        params: Dict[str, Any],
    ) -> str:
        """
        启动处理任务

        Returns:
            task_id: 任务 ID
        """
        pass

    @abstractmethod
    async def on_progress(self, task_id: str, progress: int, data: Any = None):
        """进度更新回调"""
        pass

    @abstractmethod
    async def on_complete(self, task_id: str, result: List[Dict[str, Any]]):
        """完成回调"""
        pass

    @abstractmethod
    async def on_error(self, task_id: str, error: Dict[str, str]):
        """错误回调"""
        pass
```

### 4.2 Chat Handler

```python
# backend/services/handlers/chat_handler.py

class ChatHandler(BaseHandler):
    """聊天消息处理器（流式）"""

    async def start(self, message_id: str, conversation_id: str,
                    user_id: str, content: List[Dict], params: Dict) -> str:
        # 1. 验证积分
        await self._check_credits(user_id)

        # 2. 生成 task_id
        task_id = str(uuid.uuid4())

        # 3. 保存任务到数据库
        await self._save_task(task_id, message_id, conversation_id, user_id, 'chat')

        # 4. 更新消息状态为 streaming
        await message_service.update_message(message_id, status='streaming')

        # 5. 启动流式生成（异步）
        asyncio.create_task(self._stream_generate(
            task_id, message_id, conversation_id, user_id, content, params
        ))

        return task_id

    async def _stream_generate(self, task_id: str, message_id: str,
                                conversation_id: str, user_id: str,
                                content: List[Dict], params: Dict):
        """流式生成"""
        accumulated_text = ""

        try:
            # 推送开始消息
            await ws_manager.broadcast_to_task(task_id, {
                'type': 'message_start',
                'message_id': message_id,
            })

            # 流式生成
            async for chunk in self._call_llm(content, params):
                accumulated_text += chunk

                # 推送增量
                await ws_manager.broadcast_to_task(task_id, {
                    'type': 'message_chunk',
                    'chunk': chunk,
                    'accumulated': accumulated_text,
                })

            # 完成：更新消息
            await self.on_complete(task_id, [
                {'type': 'text', 'text': accumulated_text}
            ])

        except Exception as e:
            await self.on_error(task_id, {
                'code': 'GENERATION_FAILED',
                'message': str(e),
            })

    async def on_complete(self, task_id: str, result: List[Dict]):
        """完成回调"""
        task = await self._get_task(task_id)

        # 1. 更新消息
        message = await message_service.update_message(
            message_id=task['message_id'],
            content=result,
            status='completed',
            credits_cost=task['credits_used'],
        )

        # 2. 推送完成消息
        await ws_manager.broadcast_to_task(task_id, {
            'type': 'message_done',
            'message': message.to_dict(),
        })

        # 3. 更新任务状态
        await self._complete_task(task_id)
```

### 4.3 Image Handler

```python
# backend/services/handlers/image_handler.py

class ImageHandler(BaseHandler):
    """图片生成处理器（异步任务）"""

    async def start(self, message_id: str, conversation_id: str,
                    user_id: str, content: List[Dict], params: Dict) -> str:
        # 1. 验证并扣除积分
        credits = await self._deduct_credits(user_id, params)

        # 2. 提取 prompt
        prompt = self._extract_prompt(content)

        # 3. 调用图片生成 API（带 Webhook）
        external_task_id = await self._call_image_api(
            prompt=prompt,
            params=params,
            callback_url=f"{settings.base_url}/webhooks/media/callback",
        )

        # 4. 保存任务
        await self._save_task(
            task_id=external_task_id,
            message_id=message_id,
            conversation_id=conversation_id,
            user_id=user_id,
            task_type='image',
            credits_locked=credits,
        )

        # 5. 更新消息状态
        await message_service.update_message(message_id, status='pending')

        # 6. 推送任务已提交
        await ws_manager.broadcast_to_user(user_id, {
            'type': 'message_pending',
            'task_id': external_task_id,
            'message_id': message_id,
            'estimated_time_ms': 30000,
        })

        return external_task_id

    async def on_complete(self, task_id: str, result: List[Dict]):
        """Webhook 回调时调用"""
        task = await self._get_task(task_id)

        # 1. 上传到 OSS
        oss_urls = await self._upload_to_oss(result, task['user_id'])

        # 2. 构建 content
        content_parts = [
            {'type': 'image', 'url': url} for url in oss_urls
        ]

        # 3. 更新消息
        message = await message_service.update_message(
            message_id=task['message_id'],
            content=content_parts,
            status='completed',
            credits_cost=task['credits_locked'],
        )

        # 4. 推送完成
        await ws_manager.broadcast_to_user(task['user_id'], {
            'type': 'message_done',
            'task_id': task_id,
            'message': message.to_dict(),
        })

        # 5. 推送积分变化
        await ws_manager.broadcast_to_user(task['user_id'], {
            'type': 'credits_changed',
            'credits': await self._get_user_credits(task['user_id']),
            'delta': -task['credits_locked'],
        })
```

### 4.4 统一 Webhook 接收器

```python
# backend/api/routes/webhook.py

@router.post("/webhooks/media/callback")
async def media_callback(
    request: Request,
    signature: str = Header(..., alias="X-Signature"),
):
    """
    统一媒体生成回调

    处理 KIE 等外部服务的完成通知
    """
    # 1. 验证签名
    body = await request.body()
    if not verify_signature(body, signature, settings.webhook_secret):
        raise HTTPException(status_code=401, detail="Invalid signature")

    # 2. 解析回调数据
    data = await request.json()
    task_id = data.get('task_id')
    status = data.get('status')  # success / failed

    # 3. 获取任务信息
    task = await task_service.get_task(task_id)
    if not task:
        logger.warning(f"Task not found: {task_id}")
        return {"status": "ignored"}

    # 4. 获取对应 Handler
    handler = get_handler(task['type'])  # image / video

    # 5. 调用回调
    if status == 'success':
        result = data.get('result', {})
        urls = result.get('image_urls') or [result.get('video_url')]
        content_parts = [
            {'type': task['type'], 'url': url} for url in urls if url
        ]
        await handler.on_complete(task_id, content_parts)
    else:
        await handler.on_error(task_id, {
            'code': data.get('error_code', 'UNKNOWN'),
            'message': data.get('error_message', '生成失败'),
        })

    return {"status": "ok"}
```

---

## 五、统一 WebSocket 协议

### 5.1 消息类型

```typescript
// 统一的 WebSocket 消息类型
type WSMessageType =
  // 消息生命周期
  | 'message_pending'    // 任务已提交，等待处理
  | 'message_start'      // 开始生成（流式）
  | 'message_chunk'      // 流式内容块
  | 'message_progress'   // 进度更新（0-100）
  | 'message_done'       // 生成完成
  | 'message_error'      // 生成失败

  // 系统消息
  | 'credits_changed'    // 积分变化
  | 'notification'       // 通知

  // 连接管理
  | 'subscribe'          // 订阅任务
  | 'unsubscribe'        // 取消订阅
  | 'ping' | 'pong';     // 心跳
```

### 5.2 消息格式

```typescript
// 统一的 WebSocket 消息格式
interface WSMessage {
  type: WSMessageType;

  // 消息相关
  message_id?: string;
  message?: Message;        // 完整消息（done 时）

  // 任务相关
  task_id?: string;

  // 流式相关
  chunk?: string;           // 增量内容
  accumulated?: string;     // 累积内容

  // 进度相关
  progress?: number;        // 0-100
  estimated_time_ms?: number;

  // 错误相关
  error?: {
    code: string;
    message: string;
  };

  // 元数据
  conversation_id?: string;
  timestamp: number;
}
```

### 5.3 后端实现

```python
# backend/schemas/websocket.py

class WSMessageType(str, Enum):
    # 消息生命周期
    MESSAGE_PENDING = "message_pending"
    MESSAGE_START = "message_start"
    MESSAGE_CHUNK = "message_chunk"
    MESSAGE_PROGRESS = "message_progress"
    MESSAGE_DONE = "message_done"
    MESSAGE_ERROR = "message_error"

    # 系统消息
    CREDITS_CHANGED = "credits_changed"
    NOTIFICATION = "notification"

    # 连接管理
    SUBSCRIBE = "subscribe"
    UNSUBSCRIBE = "unsubscribe"
    PING = "ping"
    PONG = "pong"


def build_message_done(
    task_id: str,
    message: Dict[str, Any],
    conversation_id: str,
) -> Dict[str, Any]:
    """构建完成消息"""
    return {
        "type": WSMessageType.MESSAGE_DONE.value,
        "task_id": task_id,
        "message_id": message["id"],
        "message": message,
        "conversation_id": conversation_id,
        "timestamp": int(time.time() * 1000),
    }
```

---

## 六、统一前端 Store

### 6.1 MessageStore（合并后）

```typescript
// frontend/src/stores/useMessageStore.ts

import { create } from 'zustand';
import { persist } from 'zustand/middleware';

interface MessageStore {
  // ============================================================
  // 状态
  // ============================================================

  // 消息缓存: conversationId -> messages
  messages: Map<string, Message[]>;

  // 进行中的任务: taskId -> TaskState
  tasks: Map<string, TaskState>;

  // ============================================================
  // 消息操作
  // ============================================================

  // 添加消息
  addMessage: (conversationId: string, message: Message) => void;

  // 更新消息（按 ID）
  updateMessage: (messageId: string, updates: Partial<Message>) => void;

  // 更新消息内容（流式追加）
  appendContent: (messageId: string, chunk: string) => void;

  // 替换消息内容（完成时）
  setContent: (messageId: string, content: ContentPart[]) => void;

  // 设置消息状态
  setStatus: (messageId: string, status: Message['status']) => void;

  // 删除消息
  removeMessage: (messageId: string) => void;

  // ============================================================
  // 任务操作
  // ============================================================

  // 创建任务
  createTask: (task: TaskState) => void;

  // 更新任务进度
  updateTaskProgress: (taskId: string, progress: number) => void;

  // 完成任务
  completeTask: (taskId: string) => void;

  // 任务失败
  failTask: (taskId: string, error: string) => void;

  // 获取任务
  getTask: (taskId: string) => TaskState | undefined;

  // ============================================================
  // 辅助方法
  // ============================================================

  // 获取对话消息
  getMessages: (conversationId: string) => Message[];

  // 获取消息（按 ID）
  getMessage: (messageId: string) => Message | undefined;

  // 清空对话缓存
  clearConversation: (conversationId: string) => void;
}

interface TaskState {
  taskId: string;
  messageId: string;
  conversationId: string;
  type: 'chat' | 'image' | 'video' | 'audio';
  status: 'pending' | 'processing' | 'completed' | 'failed';
  progress: number;
  createdAt: number;
  error?: string;
}

export const useMessageStore = create<MessageStore>()(
  persist(
    (set, get) => ({
      messages: new Map(),
      tasks: new Map(),

      // ========================================
      // 消息操作
      // ========================================

      addMessage: (conversationId, message) => {
        set((state) => {
          const messages = new Map(state.messages);
          const list = messages.get(conversationId) || [];

          // 防止重复添加
          if (list.some(m => m.id === message.id)) {
            return state;
          }

          messages.set(conversationId, [...list, message]);
          return { messages };
        });
      },

      updateMessage: (messageId, updates) => {
        set((state) => {
          const messages = new Map(state.messages);

          for (const [convId, list] of messages) {
            const index = list.findIndex(m => m.id === messageId);
            if (index !== -1) {
              const updated = { ...list[index], ...updates, updated_at: new Date().toISOString() };
              const newList = [...list];
              newList[index] = updated;
              messages.set(convId, newList);
              break;
            }
          }

          return { messages };
        });
      },

      appendContent: (messageId, chunk) => {
        const message = get().getMessage(messageId);
        if (!message) return;

        // 找到或创建 text 部件
        const content = [...message.content];
        const textIndex = content.findIndex(p => p.type === 'text');

        if (textIndex >= 0) {
          content[textIndex] = {
            type: 'text',
            text: (content[textIndex] as TextPart).text + chunk,
          };
        } else {
          content.push({ type: 'text', text: chunk });
        }

        get().updateMessage(messageId, { content });
      },

      setContent: (messageId, content) => {
        get().updateMessage(messageId, { content, status: 'completed' });
      },

      setStatus: (messageId, status) => {
        get().updateMessage(messageId, { status });
      },

      removeMessage: (messageId) => {
        set((state) => {
          const messages = new Map(state.messages);

          for (const [convId, list] of messages) {
            const filtered = list.filter(m => m.id !== messageId);
            if (filtered.length !== list.length) {
              messages.set(convId, filtered);
              break;
            }
          }

          return { messages };
        });
      },

      // ========================================
      // 任务操作
      // ========================================

      createTask: (task) => {
        set((state) => {
          const tasks = new Map(state.tasks);
          tasks.set(task.taskId, task);
          return { tasks };
        });
      },

      updateTaskProgress: (taskId, progress) => {
        set((state) => {
          const tasks = new Map(state.tasks);
          const task = tasks.get(taskId);
          if (task) {
            tasks.set(taskId, { ...task, progress, status: 'processing' });
          }
          return { tasks };
        });
      },

      completeTask: (taskId) => {
        set((state) => {
          const tasks = new Map(state.tasks);
          tasks.delete(taskId);  // 完成后移除
          return { tasks };
        });
      },

      failTask: (taskId, error) => {
        set((state) => {
          const tasks = new Map(state.tasks);
          const task = tasks.get(taskId);
          if (task) {
            tasks.set(taskId, { ...task, status: 'failed', error });
          }
          return { tasks };
        });
      },

      getTask: (taskId) => get().tasks.get(taskId),

      // ========================================
      // 辅助方法
      // ========================================

      getMessages: (conversationId) => get().messages.get(conversationId) || [],

      getMessage: (messageId) => {
        for (const list of get().messages.values()) {
          const found = list.find(m => m.id === messageId);
          if (found) return found;
        }
        return undefined;
      },

      clearConversation: (conversationId) => {
        set((state) => {
          const messages = new Map(state.messages);
          messages.delete(conversationId);
          return { messages };
        });
      },
    }),
    {
      name: 'message-store',
      // 自定义序列化（Map 不能直接 JSON 序列化）
      storage: {
        getItem: (name) => {
          const str = localStorage.getItem(name);
          if (!str) return null;
          const { state } = JSON.parse(str);
          return {
            state: {
              ...state,
              messages: new Map(state.messages || []),
              tasks: new Map(state.tasks || []),
            },
          };
        },
        setItem: (name, value) => {
          const str = JSON.stringify({
            state: {
              ...value.state,
              messages: Array.from(value.state.messages.entries()),
              tasks: Array.from(value.state.tasks.entries()),
            },
          });
          localStorage.setItem(name, str);
        },
        removeItem: (name) => localStorage.removeItem(name),
      },
    }
  )
);
```

### 6.2 WebSocket Context（简化版）

```typescript
// frontend/src/contexts/WebSocketContext.tsx

export function WebSocketProvider({ children }: { children: ReactNode }) {
  const ws = useWebSocket();
  const messageStore = useMessageStore();

  useEffect(() => {
    // 统一消息处理
    const handlers: Record<string, (msg: WSMessage) => void> = {

      // 消息开始（流式）
      message_start: (msg) => {
        if (msg.message_id) {
          messageStore.setStatus(msg.message_id, 'streaming');
        }
      },

      // 流式内容块
      message_chunk: (msg) => {
        if (msg.message_id && msg.chunk) {
          messageStore.appendContent(msg.message_id, msg.chunk);
        }
      },

      // 进度更新
      message_progress: (msg) => {
        if (msg.task_id && msg.progress !== undefined) {
          messageStore.updateTaskProgress(msg.task_id, msg.progress);
        }
      },

      // 生成完成
      message_done: (msg) => {
        if (msg.message) {
          // 直接用后端返回的完整消息替换
          messageStore.updateMessage(msg.message.id, msg.message);
        }
        if (msg.task_id) {
          messageStore.completeTask(msg.task_id);
        }

        // Toast 提示
        toast.success('生成完成');
      },

      // 生成失败
      message_error: (msg) => {
        if (msg.message_id && msg.error) {
          messageStore.updateMessage(msg.message_id, {
            status: 'failed',
            error: msg.error,
          });
        }
        if (msg.task_id) {
          messageStore.failTask(msg.task_id, msg.error?.message || '生成失败');
        }

        toast.error(msg.error?.message || '生成失败');
      },

      // 积分变化
      credits_changed: (msg) => {
        if (msg.credits !== undefined) {
          useAuthStore.getState().updateCredits(msg.credits);
        }
      },
    };

    // 注册所有处理器
    const unsubscribes = Object.entries(handlers).map(([type, handler]) =>
      ws.subscribe(type as WSMessageType, handler)
    );

    return () => unsubscribes.forEach(unsub => unsub());
  }, [ws, messageStore]);

  return (
    <WebSocketContext.Provider value={{ ws, subscribeTask: ws.subscribeTask }}>
      {children}
    </WebSocketContext.Provider>
  );
}
```

---

## 七、统一发送器

```typescript
// frontend/src/services/messageSender.ts

import { useMessageStore } from '../stores/useMessageStore';
import { generateMessage } from './api';

interface SendOptions {
  conversationId: string;
  content: ContentPart[];
  generationType?: 'chat' | 'image' | 'video';
  model?: string;
  params?: Record<string, unknown>;
  operation?: 'send' | 'regenerate' | 'retry';
  originalMessageId?: string;
}

export async function sendMessage(options: SendOptions): Promise<void> {
  const {
    conversationId,
    content,
    generationType,
    model,
    params,
    operation = 'send',
    originalMessageId,
  } = options;

  const messageStore = useMessageStore.getState();
  const clientRequestId = crypto.randomUUID();

  // 1. 乐观添加用户消息
  const userMessage: Message = {
    id: `temp-${clientRequestId}`,
    conversation_id: conversationId,
    role: 'user',
    content,
    status: 'completed',
    created_at: new Date().toISOString(),
  };

  if (operation !== 'retry') {
    messageStore.addMessage(conversationId, userMessage);
  }

  // 2. 乐观添加助手占位符
  const placeholderMessage: Message = {
    id: `pending-${clientRequestId}`,
    conversation_id: conversationId,
    role: 'assistant',
    content: [],
    status: 'pending',
    created_at: new Date(Date.now() + 1).toISOString(),
  };

  messageStore.addMessage(conversationId, placeholderMessage);

  try {
    // 3. 调用 API
    const response = await generateMessage(conversationId, {
      operation,
      content,
      generation_type: generationType,
      model,
      params,
      original_message_id: originalMessageId,
      client_request_id: clientRequestId,
    });

    // 4. 替换临时 ID 为真实 ID
    if (response.user_message) {
      messageStore.updateMessage(`temp-${clientRequestId}`, {
        id: response.user_message.id,
      });
    }

    messageStore.updateMessage(`pending-${clientRequestId}`, {
      id: response.assistant_message.id,
      task_id: response.task_id,
    });

    // 5. 创建任务追踪
    messageStore.createTask({
      taskId: response.task_id,
      messageId: response.assistant_message.id,
      conversationId,
      type: generationType || 'chat',
      status: 'pending',
      progress: 0,
      createdAt: Date.now(),
    });

    // 6. WebSocket 订阅任务
    ws.subscribeTask(response.task_id);

  } catch (error) {
    // 7. 错误处理：移除占位符，添加错误消息
    messageStore.removeMessage(`pending-${clientRequestId}`);
    messageStore.addMessage(conversationId, {
      id: `error-${clientRequestId}`,
      conversation_id: conversationId,
      role: 'assistant',
      content: [{ type: 'text', text: '' }],
      status: 'failed',
      error: {
        code: 'SEND_FAILED',
        message: error instanceof Error ? error.message : '发送失败',
      },
      created_at: new Date().toISOString(),
    });

    throw error;
  }
}
```

---

## 八、文件变更清单

### 8.1 后端

| 文件 | 变更类型 | 说明 |
|------|---------|------|
| `schemas/message.py` | **重写** | 统一 Message 模型 |
| `schemas/websocket.py` | **重写** | 统一 WebSocket 消息类型 |
| `api/routes/message.py` | **重写** | 统一 `/generate` 端点 |
| `api/routes/webhook.py` | **新增** | 统一 Webhook 接收器 |
| `services/handlers/` | **新增** | Handler 目录 |
| `services/handlers/base.py` | **新增** | Handler 基类 |
| `services/handlers/chat_handler.py` | **新增** | 聊天 Handler |
| `services/handlers/image_handler.py` | **新增** | 图片 Handler |
| `services/handlers/video_handler.py` | **新增** | 视频 Handler |
| `services/message_service.py` | 修改 | 适配新模型 |
| `services/background_task_worker.py` | 简化 | 只保留超时清理 |

### 8.2 前端

| 文件 | 变更类型 | 说明 |
|------|---------|------|
| `stores/useMessageStore.ts` | **新增** | 统一 Store |
| `stores/useChatStore.ts` | **删除** | 合并到 useMessageStore |
| `stores/useTaskStore.ts` | **删除** | 合并到 useMessageStore |
| `stores/useConversationRuntimeStore.ts` | **删除** | 合并到 useMessageStore |
| `contexts/WebSocketContext.tsx` | **重写** | 简化消息处理 |
| `services/messageSender.ts` | **重写** | 统一发送器 |
| `services/messageSender/` | **删除** | 目录移除 |
| `components/chat/MessageItem.tsx` | 修改 | 适配新 content 格式 |
| `components/chat/MessageMedia.tsx` | 修改 | 从 content 数组提取媒体 |
| `utils/taskRestoration.ts` | 简化 | 使用统一任务恢复 |

### 8.3 数据库

```sql
-- 迁移文件
migrations/
  ├── 001_add_content_jsonb.sql      -- 添加新字段
  ├── 002_migrate_content_data.sql   -- 数据迁移
  └── 003_drop_legacy_columns.sql    -- 删除旧字段（可选）
```

---

## 九、实施计划

| 阶段 | 时间 | 内容 | 风险 |
|------|------|------|------|
| **Phase 1** | 2天 | 数据库迁移 + 后端 Message 模型 | 低 |
| **Phase 2** | 3天 | Handler 架构 + Webhook 端点 | 中 |
| **Phase 3** | 3天 | 前端 Store 合并 + 发送器重写 | 高 |
| **Phase 4** | 2天 | WebSocket 协议统一 | 中 |
| **Phase 5** | 2天 | 组件适配 + 端到端测试 | 低 |
| **Phase 6** | 1天 | 灰度发布 + 监控 | 低 |

**总计**: 约 13 天

---

## 十、回退方案

如果统一架构风险过高，可以分步实施：

1. **第一步**: 只改消息模型（`content: ContentPart[]`），其他不变
2. **第二步**: 统一 WebSocket 消息类型
3. **第三步**: 合并 Store
4. **第四步**: 统一 Handler

每一步都可以独立发布和验证。

---

## 参考资料

- [OpenAI GPT-4o](https://openai.com/index/hello-gpt-4o/) - 统一多模态模型
- [LobeChat Architecture](https://lobehub.com/docs/development/basic/architecture) - 开源架构参考
- [Replicate Webhooks](https://replicate.com/docs/topics/webhooks) - Webhook 最佳实践
- [LangChain Multimodal](https://python.langchain.com/docs/how_to/multimodal_inputs/) - 多模态输入处理
