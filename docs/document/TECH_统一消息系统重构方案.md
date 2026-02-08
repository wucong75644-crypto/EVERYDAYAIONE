# 统一消息系统重构方案

> **版本**：v1.0 | **日期**：2026-02-06 | **状态**：待确认
>
> **范围**：前端统一发送器 + 后端统一适配器 + WebSocket 整合

---

## 一、概述

### 1.1 重构目标

将现有的**分散架构**重构为**统一架构**，实现：

```
【现有架构】分散、重复、难扩展
┌─────────────┐  ┌─────────────┐  ┌─────────────┐
│ chatSender  │  │ mediaSender │  │ regenerate  │
│ (206行)     │  │ (124行)     │  │ (多个文件)   │
└──────┬──────┘  └──────┬──────┘  └──────┬──────┘
       │                │                │
       ▼                ▼                ▼
┌─────────────┐  ┌─────────────┐  ┌─────────────┐
│ POST /send  │  │ POST /image │  │ POST /regen │
└──────┬──────┘  └──────┬──────┘  └──────┬──────┘
       │                │                │
       ▼                ▼                ▼
┌─────────────────────────────────────────────────┐
│           KieChatAdapter（硬编码）               │
└─────────────────────────────────────────────────┘


【目标架构】统一、简洁、易扩展
┌─────────────────────────────────────────────────┐
│         sendUnifiedMessage() - 统一入口         │
│         type: 'chat' | 'image' | 'video'        │
│         operation: 'send' | 'regenerate' | 'retry'
└────────────────────────┬────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────┐
│         POST /conversations/{id}/tasks          │
│         统一任务创建 API                         │
└────────────────────────┬────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────┐
│         创建聊天适配器(model_id)                 │
│         自动路由到对应 Provider                  │
└────────────────────────┬────────────────────────┘
                         │
         ┌───────────────┼───────────────┐
         ▼               ▼               ▼
┌─────────────┐  ┌─────────────┐  ┌─────────────┐
│ KIE 适配器  │  │Google 适配器│  │OpenAI(预留) │
└─────────────┘  └─────────────┘  └─────────────┘
```

### 1.2 核心收益

| 维度 | 现有架构 | 目标架构 | 收益 |
|------|---------|---------|------|
| **前端代码量** | ~1500行，10+文件 | ~1000行，7文件 | -33% |
| **后端扩展性** | 新增 Provider 需改多处 | 3 步即可 | 极大提升 |
| **消息类型扩展** | 每种类型独立实现 | 统一接口 | 极大提升 |
| **重新生成逻辑** | 分散在多处 | 统一入口 | 易维护 |
| **WebSocket 处理** | 聊天/媒体分开 | 统一处理 | 简化 |

### 1.3 设计理念（借鉴 Git）

| Git 概念 | 消息系统映射 |
|---------|-------------|
| **Commit Hash** | `task_id` - 唯一标识一次操作 |
| **Working → Staged → Committed** | `optimistic → pending → confirmed → completed` |
| **Branch** | 每个 `task_id` 对应独立的状态流转 |
| **Idempotent** | 相同 `task_id` 不会产生重复消息 |

---

## 二、整体架构

### 2.1 系统架构图

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              前端 (React)                                    │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  ┌────────────────────────────────────────────────────────────────────────┐ │
│  │                    统一发送入口 (unifiedSender.ts)                      │ │
│  │                                                                         │ │
│  │  sendUnifiedMessage({ type, operation, conversationId, content, ... }) │ │
│  │                                                                         │ │
│  │  type: 'chat' | 'image' | 'video' | 'audio'(预留)                      │ │
│  │  operation: 'send' | 'regenerate' | 'retry'                            │ │
│  └───────────────────────────────┬────────────────────────────────────────┘ │
│                                  │                                           │
│  ┌───────────────────────────────▼────────────────────────────────────────┐ │
│  │                 消息生命周期管理 (lifecycle.ts)                         │ │
│  │                                                                         │ │
│  │  Phase 1: OPTIMISTIC (乐观更新)                                        │ │
│  │    └─ temp-{uuid} 用户消息 + streaming-{tempTaskId} 占位符             │ │
│  │                                                                         │ │
│  │  Phase 2: PENDING (后端确认)                                           │ │
│  │    └─ 替换为真实 ID，绑定 task_id                                      │ │
│  │                                                                         │ │
│  │  Phase 3: CONFIRMED (流式进行中)                                       │ │
│  │    └─ WebSocket 接收 chat_chunk / task_progress                        │ │
│  │                                                                         │ │
│  │  Phase 4: COMPLETED (完成)                                             │ │
│  │    └─ 持久化到 ChatStore，清理占位符                                   │ │
│  └───────────────────────────────┬────────────────────────────────────────┘ │
│                                  │                                           │
│  ┌───────────────────────────────▼────────────────────────────────────────┐ │
│  │               WebSocket Context (统一消息处理)                          │ │
│  │                                                                         │ │
│  │  消息类型:                                                              │ │
│  │  ├─ chat_start / chat_chunk / chat_done / chat_error                   │ │
│  │  ├─ task_status (media: completed/failed)                              │ │
│  │  ├─ credits_changed                                                    │ │
│  │  └─ subscribed (恢复任务时的累积内容)                                  │ │
│  │                                                                         │ │
│  │  关键机制:                                                              │ │
│  │  ├─ registerOperation(taskId, callbacks) - 操作上下文注册              │ │
│  │  └─ 统一处理聊天完成和媒体完成                                         │ │
│  └────────────────────────────────────────────────────────────────────────┘ │
│                                                                              │
└──────────────────────────────────┬───────────────────────────────────────────┘
                                   │
                                   │ HTTP + WebSocket
                                   │
┌──────────────────────────────────▼───────────────────────────────────────────┐
│                              后端 (FastAPI)                                   │
├──────────────────────────────────────────────────────────────────────────────┤
│                                                                               │
│  ┌─────────────────────────────────────────────────────────────────────────┐ │
│  │                    统一任务 API (/conversations/{id}/tasks)              │ │
│  │                                                                          │ │
│  │  POST: 创建任务 (type: chat/image/video, operation: send/regenerate)    │ │
│  │  GET /pending: 获取进行中任务 (恢复用)                                   │ │
│  └──────────────────────────────┬──────────────────────────────────────────┘ │
│                                 │                                            │
│  ┌──────────────────────────────▼──────────────────────────────────────────┐ │
│  │                    统一适配器工厂 (adapters/工厂.py)                      │ │
│  │                                                                          │ │
│  │  创建聊天适配器(model_id) → 自动路由到对应 Provider                     │ │
│  │                                                                          │ │
│  │  模型注册表:                                                             │ │
│  │  ├─ gemini-3-pro    → KIE Provider                                      │ │
│  │  ├─ gemini-3-flash  → KIE Provider                                      │ │
│  │  ├─ gemini-2.5-flash → Google Provider                                  │ │
│  │  └─ gpt-4o          → OpenAI Provider (预留)                            │ │
│  └──────────────────────────────┬──────────────────────────────────────────┘ │
│                                 │                                            │
│         ┌───────────────────────┼───────────────────────┐                   │
│         ▼                       ▼                       ▼                   │
│  ┌─────────────┐         ┌─────────────┐         ┌─────────────┐           │
│  │ KIE 适配器  │         │Google 适配器│         │OpenAI(预留) │           │
│  │             │         │             │         │             │           │
│  │ 流式聊天()  │         │ 流式聊天()  │         │ 流式聊天()  │           │
│  │ 估算成本()  │         │ 估算成本()  │         │ 估算成本()  │           │
│  └─────────────┘         └─────────────┘         └─────────────┘           │
│                                                                              │
│  ┌─────────────────────────────────────────────────────────────────────────┐ │
│  │                    WebSocket Manager (连接管理)                          │ │
│  │                                                                          │ │
│  │  - 连接池管理 (user_id → connections)                                   │ │
│  │  - 任务订阅 (task_id → subscribers)                                     │ │
│  │  - 消息缓冲 (断点续传)                                                  │ │
│  │  - 统一推送 (send_to_task_subscribers)                                  │ │
│  └─────────────────────────────────────────────────────────────────────────┘ │
│                                                                               │
└───────────────────────────────────────────────────────────────────────────────┘
```

### 2.2 消息状态机

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                          消息状态机 (借鉴 Git)                               │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│   ┌──────────────┐                                                          │
│   │  OPTIMISTIC  │ ← 用户操作触发，立即显示（类似 Git working directory）   │
│   │  (乐观状态)   │                                                          │
│   │              │   用户消息 ID: temp-{uuid}                               │
│   │              │   占位符 ID: streaming-{tempTaskId}                       │
│   │              │   存储: RuntimeStore (内存)                               │
│   └──────┬───────┘                                                          │
│          │                                                                   │
│          │ POST /tasks → 返回 task_id, user_message                         │
│          ▼                                                                   │
│   ┌──────────────┐                                                          │
│   │   PENDING    │ ← 后端已创建任务（类似 Git staged）                      │
│   │  (待处理)    │                                                          │
│   │              │   用户消息 ID: 替换为后端返回的真实 ID                    │
│   │              │   占位符 ID: streaming-{real_task_id}                     │
│   │              │   核心引用: task_id（唯一！）                             │
│   └──────┬───────┘                                                          │
│          │                                                                   │
│          │ WebSocket subscribe(task_id) + 后端开始处理                      │
│          ▼                                                                   │
│   ┌──────────────┐                                                          │
│   │  CONFIRMED   │ ← 后端正在处理（类似 Git commit 中）                     │
│   │  (已确认)    │                                                          │
│   │              │   聊天: chat_chunk 流式内容                               │
│   │              │   媒体: task_progress 进度更新                            │
│   └──────┬───────┘                                                          │
│          │                                                                   │
│          │ chat_done / task_status:completed                                │
│          ▼                                                                   │
│   ┌──────────────┐                                                          │
│   │  COMPLETED   │ ← 已持久化到数据库（类似 Git committed）                 │
│   │  (已完成)    │                                                          │
│   │              │   消息 ID: 后端生成的 UUID                                │
│   │              │   存储: ChatStore (持久化)                                │
│   └──────────────┘                                                          │
│                                                                              │
│   ⚠️ 任何阶段失败 → FAILED 状态（可触发 retry）                             │
│   ┌──────────────┐                                                          │
│   │    FAILED    │ ← is_error=true，可从此状态触发 retry                    │
│   │   (已失败)   │                                                          │
│   └──────────────┘                                                          │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 三、前端详细设计

### 3.1 统一发送器 (unifiedSender.ts)

```typescript
// frontend/src/services/messageSender/unifiedSender.ts

/**
 * 统一消息发送器
 *
 * 设计原则：
 * 1. 单一入口：所有消息类型都通过这个函数
 * 2. 原子操作：要么成功，要么回滚
 * 3. 幂等性：相同 taskId 不会产生重复消息
 */

import { createMessageLifecycle, updatePlaceholderId } from './lifecycle';
import { callBackendAPI } from './backendAPI';
import { createOptimisticUserMessage, createStreamingPlaceholder } from '../../utils/messageFactory';

export type MessageType = 'chat' | 'image' | 'video' | 'audio';
export type MessageOperation = 'send' | 'regenerate' | 'retry';

export interface UnifiedMessageParams {
  // === 基础参数 ===
  operation: MessageOperation;
  type: MessageType;
  conversationId: string;
  content: string;

  // === 可选参数 ===
  imageUrl?: string;
  modelId?: string;

  // === 类型特定参数 ===
  chatParams?: {
    thinkingEffort?: string;
    deepThinkMode?: boolean;
  };
  imageParams?: {
    size?: string;
    aspectRatio?: string;
  };
  videoParams?: {
    resolution?: string;
    duration?: number;
  };

  // === 重新生成专用 ===
  originalMessage?: Message;

  // === WebSocket 订阅 ===
  subscribeTask: (taskId: string, conversationId: string) => void;
  registerOperation: (taskId: string, context: OperationContext) => void;

  // === 回调 ===
  callbacks: UnifiedCallbacks;
}

export interface UnifiedCallbacks {
  onOptimisticUpdate: (userMessage: Message | null, placeholder: Message | null) => void;
  onUserConfirmed: (realUserMessage: Message) => void;
  onStreamChunk?: (chunk: string, accumulated: string) => void;
  onComplete: (finalMessage: Message) => void;
  onError: (error: Error, errorMessage: Message) => void;
}

export async function sendUnifiedMessage(params: UnifiedMessageParams): Promise<void> {
  const {
    operation, type, conversationId, content,
    subscribeTask, registerOperation, callbacks
  } = params;

  // ========================================
  // Phase 1: 生成生命周期标识
  // ========================================
  const lifecycle = createMessageLifecycle();

  // ========================================
  // Phase 2: 乐观更新
  // ========================================
  const shouldCreateOptimisticUser = operation === 'send';
  const shouldCreatePlaceholder = true;

  let optimisticUser: Message | null = null;
  if (shouldCreateOptimisticUser) {
    optimisticUser = createOptimisticUserMessage({
      content,
      conversationId,
      imageUrl: params.imageUrl ?? null,
      timestamp: lifecycle.timestamps.user,
      clientRequestId: lifecycle.clientRequestId,
    });
  }

  let placeholder: Message | null = null;
  if (shouldCreatePlaceholder) {
    const initialContent = getInitialContent(type, operation);
    placeholder = createStreamingPlaceholder({
      conversationId,
      streamingId: lifecycle.tempTaskId,
      initialContent,
      timestamp: lifecycle.timestamps.placeholder,
    });
  }

  // 触发乐观更新
  callbacks.onOptimisticUpdate(optimisticUser, placeholder);

  // ========================================
  // Phase 3: 调用后端 API
  // ========================================
  try {
    const response = await callBackendAPI(params, lifecycle);

    // 确认用户消息
    if (shouldCreateOptimisticUser && response.userMessage) {
      callbacks.onUserConfirmed(response.userMessage);
    }

    // 更新占位符 ID（temp → real task_id）
    if (shouldCreatePlaceholder) {
      updatePlaceholderId(conversationId, lifecycle.tempTaskId, response.taskId);
    }

    // 注册操作上下文（供 WebSocket 完成时回调）
    registerOperation(response.taskId, {
      type,
      operation,
      conversationId,
      onComplete: callbacks.onComplete,
      onStreamChunk: callbacks.onStreamChunk,
    });

    // 订阅 WebSocket
    subscribeTask(response.taskId, conversationId);

  } catch (error) {
    const errorMessage = createErrorMessage(conversationId, error);
    callbacks.onError(error as Error, errorMessage);
    cleanupOptimisticMessages(conversationId, lifecycle);
  }
}

function getInitialContent(type: MessageType, operation: MessageOperation): string {
  if (operation === 'retry') return '';

  switch (type) {
    case 'chat': return '';
    case 'image': return '图片生成中...';
    case 'video': return '视频生成中...';
    case 'audio': return '音频生成中...';
    default: return '';
  }
}
```

### 3.2 消息生命周期管理 (lifecycle.ts)

```typescript
// frontend/src/services/messageSender/lifecycle.ts

/**
 * 消息生命周期管理
 *
 * 类似 Git 的 commit hash 预计算，
 * 在操作开始前生成所有需要的唯一标识
 */

export interface MessageLifecycle {
  // 临时标识（乐观阶段）
  tempUserId: string;        // temp-{uuid}
  tempTaskId: string;        // temp-{uuid}
  clientRequestId: string;   // req-{uuid}

  // 时间戳（严格递增）
  timestamps: {
    user: string;            // ISO 时间戳
    placeholder: string;     // ISO 时间戳（比 user 晚 1ms）
  };

  // 真实标识（后端返回后填充）
  taskId: string | null;
  realUserId: string | null;
  streamingId: string | null;
  finalMessageId: string | null;
}

export function createMessageLifecycle(): MessageLifecycle {
  const uuid1 = crypto.randomUUID();
  const uuid2 = crypto.randomUUID();
  const now = Date.now();

  return {
    tempUserId: `temp-${uuid1}`,
    tempTaskId: `temp-${uuid2}`,
    clientRequestId: `req-${uuid1}`,

    timestamps: {
      user: new Date(now).toISOString(),
      placeholder: new Date(now + 1).toISOString(),
    },

    taskId: null,
    realUserId: null,
    streamingId: null,
    finalMessageId: null,
  };
}

/**
 * 更新占位符 ID
 * temp-xxx → streaming-{real_task_id}
 */
export function updatePlaceholderId(
  conversationId: string,
  tempTaskId: string,
  realTaskId: string
): void {
  const runtimeStore = useConversationRuntimeStore.getState();

  // 找到临时占位符，更新其 ID
  const state = runtimeStore.getState(conversationId);
  const tempId = `streaming-${tempTaskId}`;
  const realId = `streaming-${realTaskId}`;

  const hasTemp = state.optimisticMessages.some(m => m.id === tempId);
  if (hasTemp) {
    // 替换 ID
    runtimeStore.updateStreamingId(conversationId, tempId, realId);
  }
}
```

### 3.3 统一重新生成入口

```typescript
// frontend/src/utils/regenerate/index.ts

/**
 * 统一重新生成入口
 *
 * 根据消息状态自动选择策略：
 * - is_error=true → retry（原地重试）
 * - is_error=false → regenerate（新增消息）
 */

import { sendUnifiedMessage, MessageOperation } from '../../services/messageSender';

export async function regenerateMessage(
  targetMessage: Message,
  userMessage: Message,
  context: RegenerateContext
): Promise<void> {
  const { subscribeTask, registerOperation, setMessages } = context;

  // 1. 判断消息类型
  const type = determineMessageType(targetMessage);

  // 2. 判断操作类型
  const operation: MessageOperation = targetMessage.is_error ? 'retry' : 'regenerate';

  // 3. 创建回调
  const callbacks = createRegenerateCallbacks(
    operation,
    targetMessage,
    userMessage,
    setMessages
  );

  // 4. 调用统一发送器
  await sendUnifiedMessage({
    operation,
    type,
    conversationId: targetMessage.conversation_id,
    content: userMessage.content,
    imageUrl: userMessage.image_url ?? undefined,
    modelId: extractModelId(targetMessage),
    originalMessage: targetMessage,
    subscribeTask,
    registerOperation,
    callbacks,
    ...(type === 'chat' && { chatParams: extractChatParams(targetMessage) }),
    ...(type === 'image' && { imageParams: extractImageParams(targetMessage) }),
    ...(type === 'video' && { videoParams: extractVideoParams(targetMessage) }),
  });
}

function createRegenerateCallbacks(
  operation: MessageOperation,
  originalMessage: Message,
  userMessage: Message,
  setMessages: SetMessagesFunction
): UnifiedCallbacks {

  if (operation === 'retry') {
    // ========================================
    // 失败消息重试：原地替换
    // ========================================
    return {
      onOptimisticUpdate: () => {
        // 原地更新为"生成中"状态
        setMessages(prev => prev.map(m =>
          m.id === originalMessage.id
            ? { ...m, content: '', is_error: false }
            : m
        ));
      },

      onUserConfirmed: () => {},

      onStreamChunk: (chunk, accumulated) => {
        setMessages(prev => prev.map(m =>
          m.id === originalMessage.id
            ? { ...m, content: accumulated }
            : m
        ));
      },

      onComplete: (finalMessage) => {
        setMessages(prev => prev.map(m =>
          m.id === originalMessage.id
            ? { ...finalMessage, id: originalMessage.id }
            : m
        ));
      },

      onError: (error, errorMessage) => {
        setMessages(prev => prev.map(m =>
          m.id === originalMessage.id
            ? { ...originalMessage }
            : m
        ));
      },
    };

  } else {
    // ========================================
    // 成功消息重新生成：新增消息
    // ========================================
    let tempUserMsgId: string | null = null;
    let placeholderId: string | null = null;

    return {
      onOptimisticUpdate: (optimisticUser, placeholder) => {
        tempUserMsgId = optimisticUser?.id ?? null;
        placeholderId = placeholder?.id ?? null;

        setMessages(prev => [
          ...prev,
          ...(optimisticUser ? [optimisticUser] : []),
          ...(placeholder ? [placeholder] : []),
        ]);
      },

      onUserConfirmed: (realUserMessage) => {
        if (tempUserMsgId) {
          setMessages(prev => prev.map(m =>
            m.id === tempUserMsgId ? realUserMessage : m
          ));
        }
      },

      onStreamChunk: (chunk, accumulated) => {
        if (placeholderId) {
          setMessages(prev => prev.map(m =>
            m.id === placeholderId
              ? { ...m, content: accumulated }
              : m
          ));
        }
      },

      onComplete: (finalMessage) => {
        if (placeholderId) {
          setMessages(prev => prev.map(m =>
            m.id === placeholderId ? finalMessage : m
          ));
        }
      },

      onError: () => {
        setMessages(prev => prev.filter(m =>
          m.id !== tempUserMsgId && m.id !== placeholderId
        ));
      },
    };
  }
}
```

### 3.4 WebSocket Context 改造

```typescript
// frontend/src/contexts/WebSocketContext.tsx

/**
 * WebSocket Context 改造
 *
 * 关键改进：
 * 1. 添加 registerOperation 机制
 * 2. 统一处理聊天和媒体完成事件
 * 3. 解决 onMessageSent 回调缺失问题
 */

interface OperationContext {
  type: MessageType;
  operation: MessageOperation;
  conversationId: string;
  onComplete: (finalMessage: Message) => void;
  onStreamChunk?: (chunk: string, accumulated: string) => void;
}

export function WebSocketProvider({ children }: WebSocketProviderProps) {
  const ws = useWebSocket();

  // 操作上下文注册表
  const operationContextRef = useRef<Map<string, OperationContext>>(new Map());

  // 注册操作上下文
  const registerOperation = useCallback((taskId: string, context: OperationContext) => {
    operationContextRef.current.set(taskId, context);
  }, []);

  // 处理聊天流式内容
  const unsubChatChunk = ws.subscribe('chat_chunk', (msg) => {
    const { text, accumulated } = msg.payload;
    const conversationId = msg.conversation_id;
    const taskId = msg.task_id;

    if (!conversationId) return;

    // 更新 RuntimeStore
    runtimeStore.appendStreamingContent(conversationId, text);

    // 触发操作上下文回调（重新生成场景）
    const context = operationContextRef.current.get(taskId);
    if (context?.onStreamChunk) {
      context.onStreamChunk(text, accumulated);
    }
  });

  // 处理聊天完成（统一！）
  const unsubChatDone = ws.subscribe('chat_done', (msg) => {
    const { message_id, content, credits_consumed } = msg.payload;
    const conversationId = msg.conversation_id;
    const taskId = msg.task_id;

    if (!conversationId || !taskId) return;

    const state = runtimeStore.getState(conversationId);
    const streamingId = state.streamingMessageId;

    // 构建最终消息
    const finalMessage: Message = {
      id: message_id,
      conversation_id: conversationId,
      role: 'assistant',
      content,
      created_at: new Date().toISOString(),
      credits_cost: credits_consumed,
    };

    // 1. 添加正式消息到 ChatStore
    chatStore.appendMessage(conversationId, finalMessage);

    // 2. 移除流式占位符
    if (streamingId) {
      runtimeStore.removeOptimisticMessage(conversationId, streamingId);
    }

    // 3. 完成流式状态
    runtimeStore.completeStreaming(conversationId);

    // 4. 触发操作上下文回调（关键！）
    const context = operationContextRef.current.get(taskId);
    if (context?.onComplete) {
      context.onComplete(finalMessage);
    }
    operationContextRef.current.delete(taskId);

    // 5. 清理订阅
    subscribedTasksRef.current.delete(taskId);
    ws.unsubscribeTask(taskId);

    // 6. 广播
    tabSync.broadcast('message_completed', { conversationId, taskId });
  });

  // 处理任务状态（媒体完成）
  const unsubTaskStatus = ws.subscribe('task_status', async (msg) => {
    const { status, media_type, message_id, error_message } = msg.payload;
    const taskId = msg.task_id;
    const conversationId = msg.conversation_id;

    if (!taskId || !conversationId) return;

    const mediaTask = taskStore.getMediaTask(taskId);

    if (status === 'completed') {
      // 移除占位符
      if (mediaTask) {
        runtimeStore.removeOptimisticMessage(conversationId, mediaTask.placeholderId);
      }

      // 清除缓存，重新加载
      chatStore.clearConversationCache(conversationId);

      // 完成任务
      taskStore.completeMediaTask(taskId);

      // 触发操作上下文回调
      const context = operationContextRef.current.get(taskId);
      if (context?.onComplete && message_id) {
        const messages = await chatStore.loadMessages(conversationId);
        const finalMessage = messages.find(m => m.id === message_id);
        if (finalMessage) {
          context.onComplete(finalMessage);
        }
      }
      operationContextRef.current.delete(taskId);

      toast.success(`${media_type === 'image' ? '图片' : '视频'}生成完成`);

    } else if (status === 'failed') {
      // 失败处理
      if (mediaTask) {
        const errorMsg = createErrorMessage(conversationId, error_message);
        runtimeStore.replaceMediaPlaceholder(conversationId, mediaTask.placeholderId, errorMsg);
      }
      taskStore.failMediaTask(taskId);

      operationContextRef.current.delete(taskId);
    }

    tabSync.broadcast('task_completed', { conversationId, taskId });
  });

  // 暴露 registerOperation
  const contextValue: WebSocketContextValue = {
    ...ws,
    subscribeTaskWithMapping,
    registerOperation,
  };

  return (
    <WebSocketContext.Provider value={contextValue}>
      {children}
    </WebSocketContext.Provider>
  );
}
```

---

## 四、后端详细设计

### 4.1 统一任务 API

```python
# backend/api/routes/task.py

"""
统一任务 API

支持：
- 聊天任务
- 图片生成任务
- 视频生成任务
- 重新生成任务
"""

from fastapi import APIRouter, Depends
from services.adapters import 创建聊天适配器
from services.websocket_manager import ws_manager

router = APIRouter()


@router.post("/conversations/{conversation_id}/tasks")
async def create_task(
    conversation_id: UUID,
    request: CreateTaskRequest,
    current_user: User = Depends(get_current_user),
):
    """
    统一任务创建接口

    Request:
    {
        "type": "chat" | "image" | "video",
        "operation": "send" | "regenerate" | "retry",
        "content": "用户输入",
        "model_id": "gemini-3-flash",
        "params": { ... }  // 类型特定参数
    }

    Response:
    {
        "task_id": "uuid",
        "user_message": { ... },
        "assistant_message_id": "uuid"  // 聊天任务预分配
    }
    """

    # 1. 创建用户消息（send 操作时）
    user_message = None
    if request.operation == "send":
        user_message = await message_service.create_message(
            conversation_id=conversation_id,
            role="user",
            content=request.content,
            image_url=request.image_url,
            client_request_id=request.client_request_id,
            created_at=request.created_at,
        )

    # 2. 创建任务记录
    task = await task_service.create_task(
        type=request.type,
        user_id=current_user.id,
        conversation_id=conversation_id,
        request_params=request.params,
        model_id=request.model_id,
    )

    # 3. 启动后台处理
    if request.type == "chat":
        # 预分配 assistant_message_id
        assistant_message_id = request.assistant_message_id or str(uuid.uuid4())
        await task_service.update_task(
            task.id,
            assistant_message_id=assistant_message_id
        )

        # 启动聊天流处理
        asyncio.create_task(
            chat_stream_manager.process_task(task, assistant_message_id)
        )

        return {
            "task_id": str(task.id),
            "user_message": user_message,
            "assistant_message_id": assistant_message_id,
        }

    else:
        # 图片/视频任务
        asyncio.create_task(
            media_generator.process_task(task)
        )

        return {
            "task_id": str(task.id),
            "user_message": user_message,
        }


@router.get("/tasks/pending")
async def get_pending_tasks(
    current_user: User = Depends(get_current_user),
):
    """
    获取用户的进行中任务（用于刷新恢复）

    返回：所有 pending/running 状态的任务 + 最近 5 分钟内完成的任务
    """
    tasks = await task_service.get_pending_tasks(current_user.id)

    # 补充恢复所需的信息
    for task in tasks:
        if task.type == "chat":
            # 聊天任务返回累积内容
            buffer = ws_manager.get_task_buffer(str(task.id))
            if buffer:
                task.accumulated_content = buffer.accumulated_content
                task.last_index = len(buffer.messages) - 1

    return {"tasks": tasks, "count": len(tasks)}
```

### 4.2 统一适配器工厂

直接使用 `TECH_统一适配器方案.md` 中的设计，核心代码：

```python
# backend/services/adapters/工厂.py

def 创建聊天适配器(model_id: Optional[str] = None) -> 聊天适配器基类:
    """
    根据模型 ID 创建对应的聊天适配器

    自动路由到对应 Provider
    """
    settings = get_settings()

    # 获取模型配置
    实际模型ID = model_id if model_id in 模型注册表 else 默认模型ID
    config = 模型注册表[实际模型ID]

    # 根据 Provider 创建适配器
    if config.provider == 模型提供商.KIE:
        from .kie import KieClient, KieChatAdapter
        client = KieClient(settings.kie_api_key)
        return KieChatAdapter(client, config.provider_model)

    elif config.provider == 模型提供商.GOOGLE:
        from .google import GoogleChatAdapter
        return GoogleChatAdapter(config.provider_model, settings.google_api_key)

    else:
        raise ValueError(f"Provider {config.provider} 暂未实现")
```

### 4.3 ChatStreamManager 改造

```python
# backend/services/chat_stream_manager.py

async def process_task(self, task: Task, assistant_message_id: str):
    """
    处理聊天任务

    使用统一适配器工厂获取适配器
    """
    from services.adapters import 创建聊天适配器

    # 1. 创建适配器（自动路由到正确的 Provider）
    adapter = 创建聊天适配器(task.model_id)

    try:
        # 2. 更新任务状态
        await task_service.update_task(task.id, status="running")

        # 3. 广播开始事件
        await ws_manager.send_to_task_subscribers(str(task.id), {
            "type": "chat_start",
            "payload": {
                "model": task.model_id,
                "assistant_message_id": assistant_message_id,
            },
            "task_id": str(task.id),
            "conversation_id": str(task.conversation_id),
            "timestamp": int(time.time() * 1000),
        })

        # 4. 流式处理
        full_content = ""
        async for chunk in adapter.流式聊天(
            messages=task.request_params.get("messages", []),
            reasoning_effort=task.request_params.get("thinking_effort"),
            thinking_mode=task.request_params.get("thinking_mode"),
        ):
            if chunk.有内容:
                full_content += chunk.content

                # 广播内容块
                await ws_manager.send_to_task_subscribers(str(task.id), {
                    "type": "chat_chunk",
                    "payload": {
                        "text": chunk.content,
                        "accumulated": full_content,
                    },
                    "task_id": str(task.id),
                    "conversation_id": str(task.conversation_id),
                    "timestamp": int(time.time() * 1000),
                })

            # 获取最终 usage
            if chunk.有用量:
                final_usage = {
                    "prompt_tokens": chunk.prompt_tokens,
                    "completion_tokens": chunk.completion_tokens,
                }

        # 5. 创建 assistant 消息
        message = await message_service.create_message(
            id=assistant_message_id,
            conversation_id=task.conversation_id,
            role="assistant",
            content=full_content,
        )

        # 6. 计算并扣除积分
        cost = adapter.估算成本(
            final_usage["prompt_tokens"],
            final_usage["completion_tokens"]
        )
        await deduct_user_credits(task.user_id, cost.estimated_credits)

        # 7. 广播完成事件
        await ws_manager.send_to_task_subscribers(str(task.id), {
            "type": "chat_done",
            "payload": {
                "message_id": str(message.id),
                "content": full_content,
                "credits_consumed": cost.estimated_credits,
                "model": task.model_id,
            },
            "task_id": str(task.id),
            "conversation_id": str(task.conversation_id),
            "timestamp": int(time.time() * 1000),
        }, buffer=False)

        # 8. 更新任务状态
        await task_service.update_task(task.id, status="completed")

    except Exception as e:
        # 错误处理
        await ws_manager.send_to_task_subscribers(str(task.id), {
            "type": "chat_error",
            "payload": {"error": str(e)},
            "task_id": str(task.id),
            "conversation_id": str(task.conversation_id),
            "timestamp": int(time.time() * 1000),
        }, buffer=False)

        await task_service.update_task(task.id, status="failed", error_message=str(e))

    finally:
        await adapter.关闭()
```

---

## 五、文件改动清单

### 5.1 前端文件

#### 新增文件

| 文件 | 说明 |
|------|------|
| `services/messageSender/unifiedSender.ts` | 统一发送入口 |
| `services/messageSender/lifecycle.ts` | 生命周期管理 |
| `services/messageSender/backendAPI.ts` | 后端 API 调用 |
| `utils/regenerate/callbacks.ts` | 重新生成回调工厂 |

#### 删除文件

| 文件 | 原因 |
|------|------|
| `services/messageSender/chatSender.ts` | 合并到 unifiedSender |
| `services/messageSender/mediaSender.ts` | 合并到 unifiedSender |
| `services/messageSender/mediaGenerationCore.ts` | 移除轮询逻辑 |
| `hooks/regenerate/useRegenerateAsNewMessage.ts` | 合并到统一入口 |
| `utils/regenerate/regenerateAsNew.ts` | 合并到统一入口 |
| `utils/regenerate/regenerateInPlace.ts` | 合并到统一入口 |
| `utils/regenerate/strategies/chatStrategy.ts` | 统一处理 |
| `utils/regenerate/strategies/imageStrategy.ts` | 统一处理 |
| `utils/regenerate/strategies/videoStrategy.ts` | 统一处理 |

#### 重构文件

| 文件 | 改动 |
|------|------|
| `contexts/WebSocketContext.tsx` | 添加 registerOperation 机制 |
| `stores/useConversationRuntimeStore.ts` | 添加 updateStreamingId 方法 |
| `utils/taskRestoration.ts` | 简化为统一恢复逻辑 |
| `utils/messageFactory.ts` | 添加 createStreamingPlaceholder |
| `hooks/handlers/useTextMessageHandler.ts` | 使用 sendUnifiedMessage |
| `hooks/handlers/useMediaMessageHandler.ts` | 使用 sendUnifiedMessage |
| `utils/regenerate/index.ts` | 统一重新生成入口 |

### 5.2 后端文件

#### 新增文件

| 文件 | 说明 |
|------|------|
| `services/adapters/基类.py` | 适配器抽象基类 |
| `services/adapters/工厂.py` | 工厂 + 模型注册表 |
| `services/adapters/google/chat_adapter.py` | Google 官方适配器 |

#### 重构文件

| 文件 | 改动 |
|------|------|
| `services/adapters/kie/chat_adapter.py` | 继承基类 |
| `services/chat_stream_manager.py` | 使用工厂获取适配器 |
| `services/message_stream_service.py` | 使用工厂获取适配器 |
| `api/routes/task.py` | 统一任务 API |
| `api/routes/message.py` | 简化，转发到 task API |

---

## 六、代码量对比

### 6.1 前端

```
【旧架构】
├── chatSender.ts            206 行
├── mediaSender.ts           124 行
├── mediaGenerationCore.ts   406 行
├── useRegenerateAsNewMessage.ts  109 行
├── regenerateAsNew.ts       116 行
├── regenerateInPlace.ts      94 行
├── chatStrategy.ts           53 行
├── imageStrategy.ts          68 行
├── videoStrategy.ts          72 行
└── mediaRegeneration.ts     260 行
───────────────────────────────────
总计: ~1508 行，10 个文件

【新架构】
├── unifiedSender.ts         ~200 行
├── lifecycle.ts              ~80 行
├── backendAPI.ts            ~150 行
├── regenerate/index.ts      ~150 行
├── regenerate/callbacks.ts  ~120 行
├── WebSocketContext.tsx     +100 行改动
└── taskRestoration.ts       -100 行简化
───────────────────────────────────
总计: ~700 行，5 个核心文件

减少: ~800 行代码（-53%）
```

### 6.2 后端

```
【新增】
├── adapters/基类.py         ~450 行
├── adapters/工厂.py         ~250 行
└── adapters/google/         ~200 行
───────────────────────────────────
新增: ~900 行

【改动】
├── kie/chat_adapter.py      +100 行（继承基类）
├── chat_stream_manager.py   +50 行（使用工厂）
└── api/routes/task.py       +150 行（统一 API）
───────────────────────────────────
改动: ~300 行

【收益】
- 新增 Provider 从需要改多处 → 3 步完成
- 模型配置集中管理
- 消息格式转换统一
```

---

## 七、实施计划

### 7.1 分阶段实施

```
Phase 1: 后端基础设施 (2天)
├── 1.1 创建 adapters/基类.py
├── 1.2 创建 adapters/工厂.py
├── 1.3 改造 KieChatAdapter 继承基类
├── 1.4 测试现有功能不受影响
└── 预计: 4h

Phase 2: 前端统一发送器 (2天)
├── 2.1 创建 unifiedSender.ts
├── 2.2 创建 lifecycle.ts
├── 2.3 创建 backendAPI.ts
├── 2.4 改造 WebSocketContext 添加 registerOperation
├── 2.5 测试聊天发送流程
└── 预计: 6h

Phase 3: 消息处理迁移 (2天)
├── 3.1 改造 useTextMessageHandler 使用 sendUnifiedMessage
├── 3.2 改造 useMediaMessageHandler 使用 sendUnifiedMessage
├── 3.3 移除 mediaGenerationCore 轮询逻辑
├── 3.4 测试图片/视频生成
└── 预计: 6h

Phase 4: 重新生成统一 (1天)
├── 4.1 创建 regenerate/callbacks.ts
├── 4.2 重构 regenerate/index.ts
├── 4.3 删除旧的 regenerate 策略文件
├── 4.4 测试所有重新生成场景
└── 预计: 4h

Phase 5: 任务恢复简化 (1天)
├── 5.1 简化 taskRestoration.ts
├── 5.2 统一恢复逻辑
├── 5.3 测试刷新恢复
└── 预计: 3h

Phase 6: Google 适配器 (1天)
├── 6.1 实现 GoogleChatAdapter
├── 6.2 添加配置
├── 6.3 测试 Google 模型
└── 预计: 4h

Phase 7: 清理与文档 (1天)
├── 7.1 删除所有废弃文件
├── 7.2 更新 FUNCTION_INDEX.md
├── 7.3 更新 PROJECT_OVERVIEW.md
├── 7.4 端到端测试
└── 预计: 3h

总计: 10天，约 30h
```

### 7.2 风险控制

| 阶段 | 风险点 | 控制措施 |
|------|--------|---------|
| Phase 1 | 影响现有功能 | KIE 适配器只添加继承，不改现有方法 |
| Phase 2 | 发送失败 | 保留旧代码，逐步切换 |
| Phase 3 | 媒体生成失败 | WebSocket 推送完善后再移除轮询 |
| Phase 4 | 重新生成失败 | 分场景测试，回滚方案 |
| Phase 5 | 恢复失败 | API 返回完整信息，不依赖 WS |

---

## 八、测试矩阵

### 8.1 功能测试

| 场景 | send | regenerate | retry | 恢复 |
|------|------|-----------|-------|------|
| 聊天消息 | ✓ | ✓ | ✓ | ✓ |
| 图片生成 | ✓ | ✓ | ✓ | ✓ |
| 视频生成 | ✓ | ✓ | ✓ | ✓ |
| 多模态聊天 | ✓ | ✓ | ✓ | ✓ |
| 并发请求 | ✓ | ✓ | - | - |
| 网络断开 | ✓ | ✓ | ✓ | ✓ |

### 8.2 边界测试

| 场景 | 预期 |
|------|------|
| 空消息 | 前端拦截 |
| 超长消息 | 正常处理或截断 |
| 快速连续发送 | 消息顺序正确 |
| 刷新时 WS 未连接 | API 返回累积内容 |
| 重新生成时正在发送 | 独立 task_id 不冲突 |

---

## 九、边缘情况处理

### 9.1 乐观显示边缘情况

#### A. 快速连续发送

```typescript
// 问题：用户快速连续发送多条消息，顺序可能错乱
// 方案：使用递增时间戳确保顺序

let lastTimestamp = 0;

export function getIncrementalTimestampISO(): string {
  const now = Date.now();
  // 确保每次调用至少间隔 1ms
  const timestamp = Math.max(now, lastTimestamp + 1);
  lastTimestamp = timestamp;
  return new Date(timestamp).toISOString();
}

// 使用：
// 用户消息时间戳: T
// 占位符时间戳: T+1
// 下一条用户消息: T+2
// 下一条占位符: T+3
```

#### B. 网络断开时乐观消息

```typescript
// 问题：POST 请求失败，乐观消息残留
// 方案：捕获错误后立即回滚

try {
  const response = await callBackendAPI(params, lifecycle);
  // ...
} catch (error) {
  // 1. 清理乐观消息
  cleanupOptimisticMessages(conversationId, lifecycle);

  // 2. 显示错误消息（可选，不阻塞列表）
  const errorMessage = createErrorMessage(conversationId, error);
  callbacks.onError(error, errorMessage);

  // 3. 用户可以重新发送
}

function cleanupOptimisticMessages(conversationId: string, lifecycle: MessageLifecycle) {
  const runtimeStore = useConversationRuntimeStore.getState();

  // 移除临时用户消息
  runtimeStore.removeOptimisticMessage(conversationId, lifecycle.tempUserId);

  // 移除临时占位符
  runtimeStore.removeOptimisticMessage(conversationId, `streaming-${lifecycle.tempTaskId}`);

  // 重置生成状态
  runtimeStore.setGenerating(conversationId, false);
}
```

#### C. 重新生成时原消息正在流式中

```
场景：用户对消息A点击重新生成，但消息A还在流式输出

处理方案：
┌─────────────────────────────────────────────────────┐
│ 消息列表                                             │
├─────────────────────────────────────────────────────┤
│ [用户消息]                                           │
│ [消息A - 正在流式输出 task_id=abc]  ← 继续输出       │
│ [新用户消息 - temp-xxx]             ← 乐观更新       │
│ [新占位符 - streaming-xyz]          ← 新task_id      │
└─────────────────────────────────────────────────────┘

原则：
1. 每个 task_id 独立，互不干扰
2. 两个任务的 WebSocket 事件通过 task_id 路由到各自回调
3. 先完成的先显示最终消息
```

### 9.2 占位符生命周期边缘情况

#### A. 占位符ID转换时机

```
正常流程：
1. 创建临时占位符: streaming-temp-{uuid}
2. POST API 返回 task_id
3. 转换占位符ID: streaming-temp-{uuid} → streaming-{task_id}
4. 订阅 WebSocket
5. 收到 chat_chunk，追加内容
6. 收到 chat_done，移除占位符，添加正式消息

异常场景：API返回后、WS订阅前页面关闭
├─ 后端任务继续执行
├─ 重新打开页面
├─ 调用 /tasks/pending 获取进行中任务
├─ 根据 task_id 重建 streaming-{task_id} 占位符
└─ 订阅 WS，继续接收后续内容
```

#### B. 媒体任务占位符规范

```typescript
// 统一规范：所有占位符使用 streaming-{taskId} 格式

// 聊天任务
startStreaming(conversationId, taskId, {
  initialContent: '',  // 聊天为空，等待流式内容
  createdAt: timestamp,
});

// 图片任务
startStreaming(conversationId, taskId, {
  initialContent: '图片生成中...',  // 媒体显示提示文字
  createdAt: timestamp,
});

// 视频任务
startStreaming(conversationId, taskId, {
  initialContent: '视频生成中...',
  createdAt: timestamp,
});

// 好处：
// 1. ID格式统一，简化去重逻辑
// 2. 恢复流程无需区分任务类型
// 3. WebSocketContext 统一处理
```

### 9.3 刷新恢复详细流程

#### A. 整体恢复时序

```
┌─────────────────────────────────────────────────────────────────┐
│                        页面加载恢复流程                          │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  1. 页面加载                                                     │
│     │                                                            │
│     ▼                                                            │
│  2. RuntimeStore hydrate (partialize: () => ({}))               │
│     │  - 内存中无数据（设计如此）                                │
│     │  - 触发 onRehydrateStorage → setHydrateComplete()         │
│     ▼                                                            │
│  3. WebSocket 连接                                               │
│     │  - 连接成功 → setWsConnected(true)                        │
│     ▼                                                            │
│  4. TaskRestorationStore 检测到两个条件都满足                    │
│     │  - hydrateComplete === true                                │
│     │  - wsConnected === true                                    │
│     ▼                                                            │
│  5. 调用 initializeTaskRestoration()                            │
│     │                                                            │
│     ├──▶ 5.1 调用 GET /tasks/pending                            │
│     │        返回: { tasks: [...], count: N }                   │
│     │                                                            │
│     ├──▶ 5.2 遍历每个任务                                       │
│     │        │                                                   │
│     │        ├─ 聊天任务 (type === 'chat')                      │
│     │        │   1. startStreaming(convId, taskId)              │
│     │        │   2. setStreamingContent(accumulated_content)    │
│     │        │   3. subscribeTask(taskId, convId)               │
│     │        │                                                   │
│     │        └─ 媒体任务 (type === 'image' | 'video')           │
│     │            1. startStreaming(convId, taskId, {            │
│     │                 initialContent: '图片/视频生成中...'      │
│     │               })                                          │
│     │            2. addMediaTask(taskId, placeholderId)         │
│     │            3. subscribeTask(taskId, convId)               │
│     │                                                            │
│     └──▶ 5.3 标记恢复完成                                       │
│              setRestorationComplete(true)                       │
│                                                                  │
│  6. 后续 WebSocket 事件正常处理                                  │
│     - chat_chunk → appendStreamingContent                        │
│     - chat_done → 移除占位符 + 添加正式消息                      │
│     - task_status → 媒体任务完成处理                            │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

#### B. 聊天任务恢复详情

```typescript
// /tasks/pending API 返回的聊天任务结构
interface PendingChatTask {
  id: string;                    // task_id
  type: 'chat';
  status: 'pending' | 'running';
  conversation_id: string;
  model_id: string;
  assistant_message_id: string;  // 预分配的AI消息ID
  accumulated_content?: string;  // 已累积的流式内容
  last_index?: number;           // 最后一个chunk的索引
  created_at: string;
}

// 恢复处理
async function restoreChatTask(task: PendingChatTask, subscribeTask: Function) {
  const runtimeStore = useConversationRuntimeStore.getState();

  // 1. 创建占位符（使用预分配的 assistant_message_id）
  runtimeStore.startStreaming(task.conversation_id, task.assistant_message_id);

  // 2. 设置已累积的内容（如果有）
  if (task.accumulated_content) {
    runtimeStore.setStreamingContent(task.conversation_id, task.accumulated_content);
  }

  // 3. 订阅 WebSocket（后续 chunk 会继续追加）
  subscribeTask(task.id, task.conversation_id);

  // 注意：subscribed 消息可能也会返回 accumulated，作为双重保障
}
```

#### C. 媒体任务恢复详情

```typescript
// /tasks/pending API 返回的媒体任务结构
interface PendingMediaTask {
  id: string;                    // task_id
  type: 'image' | 'video';
  status: 'pending' | 'running';
  conversation_id: string;
  model_id: string;
  created_at: string;
  // 媒体任务无累积内容，只有最终结果
}

// 恢复处理
async function restoreMediaTask(task: PendingMediaTask, subscribeTask: Function) {
  const runtimeStore = useConversationRuntimeStore.getState();
  const taskStore = useTaskStore.getState();

  const placeholderId = `streaming-${task.id}`;
  const loadingText = task.type === 'image' ? '图片生成中...' : '视频生成中...';

  // 1. 创建占位符
  runtimeStore.startStreaming(task.conversation_id, task.id, {
    initialContent: loadingText,
  });

  // 2. 添加到 TaskStore（用于完成时定位占位符）
  taskStore.addMediaTask({
    taskId: task.id,
    conversationId: task.conversation_id,
    placeholderId,
    type: task.type,
  });

  // 3. 订阅 WebSocket
  subscribeTask(task.id, task.conversation_id);
}
```

#### D. 已完成但未推送的任务处理

```typescript
// 场景：任务在刷新期间完成，但 WebSocket 未连接时无法推送
// API 返回最近 5 分钟内完成的任务

interface RecentlyCompletedTask {
  id: string;
  type: 'chat' | 'image' | 'video';
  status: 'completed';
  conversation_id: string;
  completed_at: string;
  // 聊天任务包含完整消息
  message?: Message;
}

// 处理方式
async function handleRecentlyCompleted(task: RecentlyCompletedTask) {
  const chatStore = useChatStore.getState();

  // 检查本地是否已有该消息
  const messages = chatStore.getMessages(task.conversation_id);
  const exists = messages?.some(m => m.id === task.message?.id);

  if (!exists) {
    // 清除缓存，触发重新加载
    chatStore.clearConversationCache(task.conversation_id);
    chatStore.markConversationUnread(task.conversation_id);
  }
}
```

### 9.4 WebSocket 断线重连

#### A. 断线处理

```typescript
// WebSocketContext.tsx 中的断线检测

useEffect(() => {
  if (prevConnectedRef.current && !ws.isConnected) {
    // 从连接 → 断开
    logger.info('ws:connection', 'disconnected');

    // 1. 清空订阅状态（服务端已失效）
    subscribedTasksRef.current.clear();
    taskConversationMapRef.current.clear();
    operationContextRef.current.clear();

    // 2. 通知 TaskRestorationStore
    restorationStore.setWsConnected(false);

    // 3. 注意：不清空 RuntimeStore
    //    - 乐观消息保留显示
    //    - 流式内容保留当前状态
    //    - 等待重连后恢复
  }
}, [ws.isConnected]);
```

#### B. 重连处理

```typescript
useEffect(() => {
  if (!prevConnectedRef.current && ws.isConnected) {
    // 从断开 → 连接
    logger.info('ws:connection', 'reconnected');

    // 1. 重置恢复状态（如果之前已恢复过）
    if (restorationStore.restorationComplete) {
      resetForReconnect();
    }

    // 2. 通知连接成功
    restorationStore.setWsConnected(true);

    // 3. TaskRestorationStore 检测到条件满足后
    //    自动触发 initializeTaskRestoration
    //    - 获取当前进行中任务
    //    - 重新创建占位符（如有必要）
    //    - 重新订阅 WebSocket
  }
}, [ws.isConnected]);
```

#### C. 状态保持策略

```
断线期间的状态保持：

┌─────────────────────────────────────────────────────────────────┐
│ Store          │ 断线时        │ 重连后                         │
├─────────────────────────────────────────────────────────────────┤
│ RuntimeStore   │ 保持         │ 根据任务状态更新或保持          │
│ - 乐观消息     │ 保持显示     │ API 确认后可能需要更新ID        │
│ - 流式内容     │ 保持当前     │ 继续追加或设置累积内容          │
│ - isGenerating │ 保持 true    │ 任务完成后设为 false            │
├─────────────────────────────────────────────────────────────────┤
│ TaskStore      │ 保持         │ 根据任务状态更新                 │
│ - 媒体任务    │ 保持         │ 完成后清理                       │
├─────────────────────────────────────────────────────────────────┤
│ ChatStore      │ 保持         │ 可能需要清缓存重新加载          │
│ - 消息缓存    │ 保持         │ 发现缺失消息时重新加载          │
└─────────────────────────────────────────────────────────────────┘
```

### 9.5 并发场景处理

#### A. 同一对话多个任务

```
场景：用户快速发送多条消息，或同时生成图片和聊天

┌─────────────────────────────────────────────────────────────────┐
│ task_id=abc (聊天)              task_id=xyz (图片)              │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│ 用户消息1 ──┐                  用户消息2 ──┐                    │
│            │                              │                     │
│            ▼                              ▼                     │
│ streaming-abc                    streaming-xyz                   │
│     │                                │                          │
│     │ chat_chunk (text)              │ (等待完成)               │
│     ▼                                │                          │
│ 内容累积...                          │                          │
│     │                                │                          │
│     │ chat_done                      │ task_status:completed    │
│     ▼                                ▼                          │
│ 正式消息                         正式消息(图片)                  │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘

关键点：
1. 每个任务有独立的 task_id
2. WebSocket 事件通过 task_id 路由
3. operationContextRef 存储各自的回调
4. RuntimeStore 按 ID 管理各自的占位符
```

#### B. 多标签页同步

```typescript
// 任务完成时广播给其他标签页

// 发送方（完成任务的标签页）
tabSync.broadcast('message_completed', {
  conversationId,
  taskId,
  messageId: finalMessage.id
});

// 接收方（其他标签页）
tabSync.on('message_completed', ({ conversationId, messageId }) => {
  const chatStore = useChatStore.getState();
  const messages = chatStore.getMessages(conversationId);

  // 检查是否已有该消息
  if (!messages?.some(m => m.id === messageId)) {
    // 清缓存，下次进入该对话时重新加载
    chatStore.clearConversationCache(conversationId);

    // 如果当前正在该对话，立即重新加载
    if (getCurrentConversationId() === conversationId) {
      chatStore.loadMessages(conversationId);
    }
  }
});
```

#### C. 重新生成并发

```
场景：用户对消息A点击重新生成，生成过程中又对消息B点击重新生成

┌─────────────────────────────────────────────────────────────────┐
│ 消息列表状态变化                                                 │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│ T0: 初始状态                                                     │
│ ├─ [用户消息1]                                                   │
│ ├─ [AI消息A]                                                     │
│ ├─ [用户消息2]                                                   │
│ └─ [AI消息B]                                                     │
│                                                                  │
│ T1: 对消息A重新生成                                              │
│ ├─ [用户消息1]                                                   │
│ ├─ [AI消息A]                                                     │
│ ├─ [用户消息2]                                                   │
│ ├─ [AI消息B]                                                     │
│ ├─ [新用户消息-temp1]  ← task_id=abc 的乐观消息                 │
│ └─ [streaming-abc]     ← task_id=abc 的占位符                   │
│                                                                  │
│ T2: 对消息B重新生成（abc 仍在进行中）                            │
│ ├─ [用户消息1]                                                   │
│ ├─ [AI消息A]                                                     │
│ ├─ [用户消息2]                                                   │
│ ├─ [AI消息B]                                                     │
│ ├─ [新用户消息-temp1]                                            │
│ ├─ [streaming-abc]     ← 继续接收 abc 的 chunk                  │
│ ├─ [新用户消息-temp2]  ← task_id=xyz 的乐观消息                 │
│ └─ [streaming-xyz]     ← task_id=xyz 的占位符                   │
│                                                                  │
│ T3: abc 完成                                                     │
│ ├─ [用户消息1]                                                   │
│ ├─ [AI消息A]                                                     │
│ ├─ [用户消息2]                                                   │
│ ├─ [AI消息B]                                                     │
│ ├─ [真实用户消息1]     ← temp1 被确认                           │
│ ├─ [新AI消息A']        ← streaming-abc 被替换                   │
│ ├─ [新用户消息-temp2]                                            │
│ └─ [streaming-xyz]     ← 继续等待                               │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘

保证：
1. 每个 task_id 的事件独立处理
2. operationContext 按 task_id 存储回调
3. 不会错误地把 abc 的完成事件发给 xyz 的回调
```

---

## 十、确认事项

开始实施前，请确认：

1. **整体方案是否认可？**
   - 统一发送入口设计
   - 状态机设计
   - WebSocket 处理机制

2. **实施顺序是否合适？**
   - 先后端后前端
   - 分阶段验证

3. **是否需要保留旧接口过渡期？**
   - 建议保留 1-2 周

4. **Google 适配器是否本次一起实现？**
   - 可选，不影响主流程

---

**确认后开始实施 Phase 1。**
