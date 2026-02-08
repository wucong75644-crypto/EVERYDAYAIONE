# 统一任务恢复入口技术设计

> **版本**: v1.1
> **日期**: 2026-02-06
> **状态**: 待实现
> **更新**: 补充边界场景深度处理方案
> **关联文档**: [聊天任务恢复方案.md](./聊天任务恢复方案.md), [TECH_WEBSOCKET实时推送.md](./TECH_WEBSOCKET实时推送.md)

---

## 1. 背景与问题

### 1.1 当前问题

页面刷新后，streaming 占位符消失，无法恢复正在进行的聊天任务。

**根因分析**：

```
时序图：
──────────────────────────────────────────────────────────────
t=0ms     页面加载，Zustand hydrate 开始
t=50ms    WebSocket 连接成功，syncPendingTasks 创建 streaming 占位符
t=100ms   onRehydrateStorage 触发，清空 ALL optimisticMessages ← 问题点
t=150ms   占位符被删除，用户看到空白
──────────────────────────────────────────────────────────────
```

**问题代码位置**：`useConversationRuntimeStore.ts` 的 `onRehydrateStorage`

```typescript
// 当前实现：无条件清空所有乐观消息
onRehydrateStorage: () => (state) => {
  setTimeout(async () => {
    newStates.set(conversationId, {
      ...runtimeState,
      optimisticMessages: [],  // ← 问题：清空了刚恢复的 streaming 消息
      streamingMessageId: null,
      isGenerating: false,
    });
  }, 100);
},
```

### 1.2 设计目标

1. 统一任务恢复入口，避免分散逻辑
2. 解决 hydrate 与 WebSocket 的竞态条件
3. 按任务状态条件性清理乐观消息
4. 与 WebSocket 实时推送架构完美兼容

---

## 2. 架构设计

### 2.1 核心思想

引入 **TaskRestorationStore** 作为恢复状态协调器，确保：

1. **等待两个条件都满足**后再开始恢复：
   - Zustand hydrate 完成
   - WebSocket 连接就绪
2. **按任务状态条件清理**乐观消息：
   - 有进行中任务的对话：保留 streaming 占位符
   - 无任务的对话：清空过期乐观消息
3. **统一入口**管理所有恢复逻辑

### 2.2 架构图

```
┌─────────────────────────────────────────────────────────────────┐
│                        App 初始化                                │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  ┌──────────────────┐    ┌──────────────────┐                   │
│  │  Zustand Hydrate │    │  WebSocket 连接  │                   │
│  │  (100ms 延迟)     │    │  (异步连接)       │                   │
│  └────────┬─────────┘    └────────┬─────────┘                   │
│           │                       │                              │
│           │  setHydrateComplete   │  setWsConnected              │
│           ▼                       ▼                              │
│  ┌─────────────────────────────────────────────────────────────┐│
│  │              TaskRestorationStore (协调器)                   ││
│  │  ┌─────────────────────────────────────────────────────────┐││
│  │  │  状态:                                                   │││
│  │  │  - hydrateComplete: boolean                             │││
│  │  │  - wsConnected: boolean                                 │││
│  │  │  - restorationComplete: boolean                         │││
│  │  │  - pendingTasks: PendingTask[]                          │││
│  │  └─────────────────────────────────────────────────────────┘││
│  │                                                              ││
│  │  当 hydrateComplete && wsConnected 时:                       ││
│  │  → 触发 initializeTaskRestoration()                          ││
│  └─────────────────────────────────────────────────────────────┘│
│                          │                                       │
│                          ▼                                       │
│  ┌─────────────────────────────────────────────────────────────┐│
│  │              initializeTaskRestoration()                     ││
│  │  1. fetchPendingTasks() 获取进行中任务                        ││
│  │  2. 按任务状态清理乐观消息                                     ││
│  │  3. 恢复聊天任务 (创建占位符 + 订阅 WebSocket)                 ││
│  │  4. 恢复媒体任务 (创建占位符)                                  ││
│  └─────────────────────────────────────────────────────────────┘│
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

### 2.3 时序图

```
时间  Zustand          WebSocketContext      TaskRestorationStore    RuntimeStore
──────────────────────────────────────────────────────────────────────────────────
0ms   hydrate开始
50ms                   ws.connect()
80ms                   ws.onopen              setWsConnected(true)
                                              [等待 hydrate...]
100ms onRehydrate
      ↓ 仅标记状态                            setHydrateComplete(true)
      ↓ 不清理消息                            [两者都 ready!]
                                              ↓
                                              initializeTaskRestoration()
                                              ↓
                                              fetchPendingTasks()
                                              ↓
                                              对每个对话:
                                              - 有任务: 保留占位符
                                              - 无任务: 清空乐观消息
                                              ↓
                                              恢复聊天任务
                                              恢复媒体任务
                                              ↓
                                              setRestorationComplete(true)
──────────────────────────────────────────────────────────────────────────────────
```

---

## 3. 详细实现

### 3.1 新增文件：`useTaskRestorationStore.ts`

**路径**: `frontend/src/stores/useTaskRestorationStore.ts`

```typescript
/**
 * 任务恢复状态协调器
 *
 * 职责：
 * 1. 跟踪 hydrate 和 WebSocket 连接状态
 * 2. 在两者都就绪后触发统一恢复流程
 * 3. 防止重复恢复（strict mode / 多标签页）
 */

import { create } from 'zustand';
import { logger } from '../utils/logger';

interface TaskRestorationState {
  // 状态标记
  hydrateComplete: boolean;
  wsConnected: boolean;
  restorationComplete: boolean;
  restorationInProgress: boolean;

  // 操作
  setHydrateComplete: () => void;
  setWsConnected: (connected: boolean) => void;
  startRestoration: () => boolean; // 返回是否可以开始
  completeRestoration: () => void;
  reset: () => void;
}

export const useTaskRestorationStore = create<TaskRestorationState>((set, get) => ({
  hydrateComplete: false,
  wsConnected: false,
  restorationComplete: false,
  restorationInProgress: false,

  setHydrateComplete: () => {
    set({ hydrateComplete: true });
    logger.debug('task:restore', 'Hydrate complete, checking restoration readiness');
    get().tryStartRestoration();
  },

  setWsConnected: (connected: boolean) => {
    set({ wsConnected: connected });
    if (connected) {
      logger.debug('task:restore', 'WebSocket connected, checking restoration readiness');
      get().tryStartRestoration();
    }
  },

  startRestoration: () => {
    const state = get();

    // 防止重复恢复
    if (state.restorationComplete || state.restorationInProgress) {
      logger.debug('task:restore', 'Restoration already done or in progress, skipping');
      return false;
    }

    // 检查前置条件
    if (!state.hydrateComplete || !state.wsConnected) {
      logger.debug('task:restore', 'Not ready for restoration', {
        hydrateComplete: state.hydrateComplete,
        wsConnected: state.wsConnected,
      });
      return false;
    }

    set({ restorationInProgress: true });
    logger.info('task:restore', 'Starting task restoration');
    return true;
  },

  completeRestoration: () => {
    set({
      restorationComplete: true,
      restorationInProgress: false
    });
    logger.info('task:restore', 'Task restoration completed');
  },

  reset: () => {
    set({
      hydrateComplete: false,
      wsConnected: false,
      restorationComplete: false,
      restorationInProgress: false,
    });
  },

  // 内部方法：尝试启动恢复
  tryStartRestoration: () => {
    const state = get();
    if (state.hydrateComplete && state.wsConnected &&
        !state.restorationComplete && !state.restorationInProgress) {
      // 触发恢复 - 由外部监听器处理
      // 这里只是检查条件，实际恢复逻辑在 initializeTaskRestoration 中
    }
  },
}));

// 辅助 hook：检查是否可以开始恢复
export function useIsRestorationReady() {
  return useTaskRestorationStore(state =>
    state.hydrateComplete &&
    state.wsConnected &&
    !state.restorationComplete &&
    !state.restorationInProgress
  );
}
```

### 3.2 重构文件：`taskRestoration.ts`

**路径**: `frontend/src/utils/taskRestoration.ts`

**修改内容**：新增统一入口函数 `initializeTaskRestoration`

```typescript
// ... 保留现有 import 和类型定义 ...

import { useTaskRestorationStore } from '../stores/useTaskRestorationStore';

/**
 * 统一任务恢复入口
 *
 * 调用时机：hydrate 完成 AND WebSocket 连接就绪
 *
 * 职责：
 * 1. 获取所有进行中的任务
 * 2. 按任务状态条件清理乐观消息
 * 3. 恢复聊天任务（创建占位符 + WebSocket 订阅）
 * 4. 恢复媒体任务（创建占位符）
 */
export async function initializeTaskRestoration(
  subscribeToTask: (taskId: string, lastIndex?: number) => void
): Promise<void> {
  const { startRestoration, completeRestoration } = useTaskRestorationStore.getState();

  // 检查是否可以开始恢复
  if (!startRestoration()) {
    return;
  }

  try {
    // 1. 获取所有进行中的任务
    const tasks = await fetchPendingTasks();
    logger.info('task:restore', '获取进行中任务', {
      total: tasks.length,
      chat: tasks.filter(t => t.type === 'chat').length,
      media: tasks.filter(t => t.type !== 'chat').length,
    });

    // 2. 构建任务所属对话集合
    const conversationsWithTasks = new Set<string>();
    for (const task of tasks) {
      if (task.conversation_id) {
        conversationsWithTasks.add(task.conversation_id);
      }
    }

    // 3. 按条件清理乐观消息
    cleanupOptimisticMessages(conversationsWithTasks);

    // 4. 分类处理任务
    const chatTasks = tasks.filter(t => t.type === 'chat');
    const mediaTasks = tasks.filter(t => t.type === 'image' || t.type === 'video');

    // 5. 获取对话标题映射（用于媒体任务）
    const conversationTitles = new Map<string, string>();
    const { conversations } = useChatStore.getState();
    for (const conv of conversations) {
      conversationTitles.set(conv.id, conv.title);
    }

    // 6. 恢复聊天任务
    for (const task of chatTasks) {
      await restoreChatTask(task, subscribeToTask);
    }

    // 7. 恢复媒体任务（带错开延迟）
    cancelPendingRestorations(); // 取消之前的待处理恢复

    const restorePromises = mediaTasks.map((task, index) => {
      const delay = index * TASK_RESTORE_STAGGER_DELAY;
      return new Promise<void>((resolve) => {
        const timeoutId = setTimeout(() => {
          try {
            const title = conversationTitles.get(task.conversation_id) || '进行中的任务';
            restoreMediaTask(task, title);
          } catch (error) {
            logger.error('task:restore', '恢复媒体任务失败', error, { taskId: task.id });
          }
          resolve();
        }, delay);
        pendingRestoreTimeouts.push(timeoutId);
      });
    });

    await Promise.all(restorePromises);

    // 8. 显示恢复提示
    const totalRestored = chatTasks.length + mediaTasks.length;
    if (totalRestored > 0) {
      toast.success(`正在恢复 ${totalRestored} 个任务`);
    }

  } catch (error) {
    logger.error('task:restore', '任务恢复失败', error);
  } finally {
    completeRestoration();
  }
}

/**
 * 按条件清理乐观消息
 *
 * - 有进行中任务的对话：保留 streaming 相关消息
 * - 无任务的对话：清空所有乐观消息
 */
function cleanupOptimisticMessages(conversationsWithTasks: Set<string>) {
  const runtimeStore = useConversationRuntimeStore.getState();
  const allStates = runtimeStore.getAllStates();

  for (const [conversationId, runtimeState] of allStates.entries()) {
    if (!runtimeState.optimisticMessages.length) {
      continue;
    }

    if (conversationsWithTasks.has(conversationId)) {
      // 有进行中任务：只清理已确认的 temp- 消息，保留 streaming- 消息
      const filteredMessages = runtimeState.optimisticMessages.filter(msg => {
        // 保留 streaming 占位符
        if (msg.id.startsWith('streaming-')) {
          return true;
        }
        // 清理 temp- 用户消息（这些应该已被后端持久化）
        if (msg.id.startsWith('temp-')) {
          return false;
        }
        // 其他消息保留
        return true;
      });

      runtimeStore.updateConversationState(conversationId, {
        optimisticMessages: filteredMessages,
      });

      logger.debug('task:restore', '保留有任务对话的 streaming 消息', {
        conversationId,
        kept: filteredMessages.length,
        original: runtimeState.optimisticMessages.length,
      });
    } else {
      // 无进行中任务：清空所有乐观消息和 streaming 状态
      runtimeStore.updateConversationState(conversationId, {
        optimisticMessages: [],
        streamingMessageId: null,
        isGenerating: false,
      });

      logger.debug('task:restore', '清空无任务对话的乐观消息', {
        conversationId,
        cleared: runtimeState.optimisticMessages.length,
      });
    }
  }
}

/**
 * 恢复单个聊天任务
 *
 * 1. 创建 streaming 占位符
 * 2. 订阅 WebSocket 任务通道
 */
async function restoreChatTask(
  task: PendingTask,
  subscribeToTask: (taskId: string, lastIndex?: number) => void
) {
  if (!task.conversation_id) {
    logger.warn('task:restore', '聊天任务没有关联对话', { taskId: task.external_task_id });
    return;
  }

  const { addOptimisticMessage, setStreamingMessageId, setGenerating } =
    useConversationRuntimeStore.getState();

  // 创建 streaming 占位符
  const placeholderId = task.assistant_message_id || `streaming-${task.external_task_id}`;
  const initialContent = task.accumulated_content || '';

  const placeholder = createStreamingPlaceholder(
    task.conversation_id,
    placeholderId,
    initialContent || '思考中...',
    task.started_at
  );

  // 添加到 RuntimeStore
  addOptimisticMessage(task.conversation_id, placeholder);
  setStreamingMessageId(task.conversation_id, placeholderId);
  setGenerating(task.conversation_id, true);

  // 订阅 WebSocket 任务通道（支持断点续传）
  // last_index 为 -1 表示从头开始接收
  subscribeToTask(task.external_task_id, -1);

  logger.info('task:restore', '聊天任务已恢复', {
    taskId: task.external_task_id,
    conversationId: task.conversation_id,
    hasAccumulated: !!task.accumulated_content,
  });
}
```

### 3.3 修改文件：`useConversationRuntimeStore.ts`

**路径**: `frontend/src/stores/useConversationRuntimeStore.ts`

**修改内容**：简化 `onRehydrateStorage`，移除清理逻辑

```typescript
// 原来的 onRehydrateStorage 修改为：

onRehydrateStorage: () => (state) => {
  // 只标记 hydrate 完成，不做任何清理
  // 清理逻辑由 initializeTaskRestoration 统一处理
  setTimeout(() => {
    const { setHydrateComplete } = useTaskRestorationStore.getState();
    setHydrateComplete();

    logger.debug('runtime:hydrate', 'Hydrate completed, restoration store notified');
  }, 100);
},
```

### 3.4 修改文件：`WebSocketContext.tsx`

**路径**: `frontend/src/contexts/WebSocketContext.tsx`

**修改内容**：

1. 在连接成功时通知 TaskRestorationStore
2. 使用统一恢复入口替代原有的 `syncPendingTasks`

```typescript
// 在 WebSocketProvider 中添加：

import { useTaskRestorationStore, useIsRestorationReady } from '../stores/useTaskRestorationStore';
import { initializeTaskRestoration } from '../utils/taskRestoration';

// 在连接成功回调中：
const handleOpen = useCallback(() => {
  logger.info('ws:connect', 'WebSocket connected');

  // 通知恢复协调器
  const { setWsConnected } = useTaskRestorationStore.getState();
  setWsConnected(true);
}, []);

// 在断开连接时：
const handleClose = useCallback(() => {
  logger.info('ws:disconnect', 'WebSocket disconnected');

  const { setWsConnected } = useTaskRestorationStore.getState();
  setWsConnected(false);
}, []);

// 监听恢复条件并触发恢复
useEffect(() => {
  const unsubscribe = useTaskRestorationStore.subscribe(
    (state) => ({
      hydrateComplete: state.hydrateComplete,
      wsConnected: state.wsConnected,
      restorationComplete: state.restorationComplete,
      restorationInProgress: state.restorationInProgress,
    }),
    (current, prev) => {
      // 当两个条件都满足且尚未恢复时，触发恢复
      if (
        current.hydrateComplete &&
        current.wsConnected &&
        !current.restorationComplete &&
        !current.restorationInProgress &&
        // 确保是状态变化触发的（避免初始化时重复触发）
        (!prev.hydrateComplete || !prev.wsConnected)
      ) {
        initializeTaskRestoration(subscribeToTask);
      }
    },
    { fireImmediately: true }
  );

  return unsubscribe;
}, [subscribeToTask]);

// 删除原有的 syncPendingTasks 调用
// useEffect(() => {
//   if (isConnected) {
//     syncPendingTasks();  // ← 删除这个
//   }
// }, [isConnected]);
```

---

## 4. 边界情况处理（深度优化）

### 4.1 API 请求失败处理（关键）

**问题**：如果 `fetchPendingTasks` 请求超时或失败，不应清理乐观消息。

**解决方案**：

```typescript
export async function initializeTaskRestoration(
  subscribeToTask: (taskId: string, lastIndex?: number) => void
): Promise<void> {
  const { startRestoration, completeRestoration } = useTaskRestorationStore.getState();

  if (!startRestoration()) {
    return;
  }

  try {
    // 1. 获取任务，失败时保持乐观消息不变
    const tasks = await fetchPendingTasks();

    // fetchPendingTasks 内部失败返回空数组，需要区分"真的没任务"和"请求失败"
    // 方案：修改 fetchPendingTasks 返回 { tasks, success }

    // 2. 只有成功获取时才清理
    if (tasks !== null) {
      const conversationsWithTasks = new Set<string>();
      for (const task of tasks) {
        if (task.conversation_id) {
          conversationsWithTasks.add(task.conversation_id);
        }
      }
      cleanupOptimisticMessages(conversationsWithTasks);
    } else {
      // API 失败，保留所有乐观消息，标记为"待确认"状态
      logger.warn('task:restore', 'API 请求失败，保留乐观消息');
      markOptimisticMessagesAsPending();
    }

    // ... 后续恢复逻辑
  } catch (error) {
    // 异常时同样保留乐观消息
    logger.error('task:restore', '任务恢复异常，保留乐观消息', error);
  } finally {
    completeRestoration();
  }
}

// 修改 fetchPendingTasks 返回类型
export async function fetchPendingTasks(): Promise<PendingTask[] | null> {
  try {
    const response = await api.get<{ tasks: PendingTask[]; count: number }>('/tasks/pending');
    return response.data.tasks || [];
  } catch (error) {
    logger.error('task:fetch', '获取进行中任务失败', error);
    return null; // null 表示请求失败，区别于空数组
  }
}
```

### 4.2 任务终态的漏网之鱼（关键）

**问题**：任务在刷新期间刚好失败，`/tasks/pending` 不返回它，导致用户消息"莫名其妙消失"。

**解决方案**：扩展 `/tasks/pending` 的定义，包含"最近 5 分钟内终结的任务"。

**后端修改** (`backend/api/routes/tasks.py`)：

```python
@router.get("/tasks/pending")
async def get_pending_tasks(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    获取用户的活跃任务

    返回：
    - 进行中的任务 (status in ['pending', 'processing'])
    - 最近 5 分钟内终结的任务 (status in ['completed', 'failed'])
    """
    cutoff_time = datetime.utcnow() - timedelta(minutes=5)

    tasks = db.query(Task).filter(
        Task.user_id == current_user.id,
        or_(
            # 进行中的任务
            Task.status.in_(['pending', 'processing']),
            # 最近终结的任务（给前端同步状态的机会）
            and_(
                Task.status.in_(['completed', 'failed']),
                Task.updated_at >= cutoff_time
            )
        )
    ).all()

    return {"tasks": [task.to_dict() for task in tasks], "count": len(tasks)}
```

**前端处理**：

```typescript
async function restoreChatTask(task: PendingTask, subscribeToTask: Function) {
  // 检查任务状态
  if (task.status === 'failed') {
    // 任务已失败，显示错误消息而非占位符
    const errorMsg = {
      id: `error-${task.external_task_id}`,
      conversation_id: task.conversation_id,
      role: 'assistant' as const,
      content: task.error_message || '生成失败，请重试',
      created_at: task.started_at,
      is_error: true,
    };
    runtimeStore.addErrorMessage(task.conversation_id, errorMsg);
    logger.info('task:restore', '恢复失败任务为错误消息', { taskId: task.external_task_id });
    return;
  }

  if (task.status === 'completed') {
    // 任务已完成，直接刷新对话消息即可，无需占位符
    logger.info('task:restore', '任务已完成，跳过恢复', { taskId: task.external_task_id });
    return;
  }

  // 进行中的任务：创建占位符 + 订阅
  // ... 原有逻辑
}
```

### 4.3 多标签页一致性（关键）

**问题**：标签页 A 完成任务，标签页 B 还在 streaming 状态。

**解决方案**：双重保障

**方案 A：WebSocket 广播给所有连接**

后端已实现：`send_to_user()` 会发送给用户的所有 `conn_id`。

```python
# 后端：任务完成时广播给用户所有连接
async def on_task_complete(task_id: str, user_id: str, result: dict):
    await ws_manager.send_to_user(user_id, {
        "type": "chat_done",
        "task_id": task_id,
        "payload": result
    })
```

**方案 B：tabSync + storage 事件监听**

```typescript
// 在 WebSocketContext 中已有的 tabSync 广播
tabSync.broadcast('chat_completed', { conversationId, messageId });

// 其他标签页监听
useEffect(() => {
  const handleTabSync = (event: MessageEvent) => {
    if (event.data.type === 'chat_completed') {
      const { conversationId, messageId } = event.data.payload;
      // 清除该对话的 streaming 状态
      runtimeStore.clearStreaming(conversationId);
      // 刷新对话消息
      chatStore.refreshMessages(conversationId);
    }
  };

  tabSync.subscribe(handleTabSync);
  return () => tabSync.unsubscribe(handleTabSync);
}, []);
```

### 4.4 订阅时机与消息空窗期

**问题**：`fetchPendingTasks` 返回到 `subscribeTask` 成功之间可能有 20ms 的数据丢失。

**已解决**：后端 `accumulated_content` + 消息缓冲机制。

**前端处理确认**：收到 `subscribed` 时全量覆盖占位符内容。

```typescript
// 在 WebSocketContext 处理 subscribed 消息
const unsubSubscribed = ws.subscribe('subscribed', (msg) => {
  const { task_id, accumulated, current_index } = msg.payload;
  const conversationId = taskConversationMapRef.current.get(task_id);

  if (conversationId && accumulated) {
    // 关键：用 accumulated 全量覆盖当前占位符内容
    // 确保 t=0 到订阅成功时刻的内容完全对齐
    runtimeStore.setStreamingContent(conversationId, accumulated);

    logger.debug('ws:subscribed', '同步累积内容', {
      taskId: task_id,
      accumulatedLength: accumulated.length,
      currentIndex: current_index,
    });
  }
});
```

### 4.5 状态机操作顺序（关键）

**问题**：`chat_done` 处理时，如果先删除乐观消息再添加正式消息，UI 会闪烁。

**解决方案**：严格保证操作顺序

```typescript
// chat_done 处理器
const unsubChatDone = ws.subscribe('chat_done', (msg) => {
  const { conversation_id, message_id, content, credits_consumed } = msg.payload;

  // ⚠️ 关键顺序：
  // 1. 先添加正式消息到 ChatStore
  const finalMessage = {
    id: message_id,
    conversation_id,
    role: 'assistant' as const,
    content,
    created_at: new Date().toISOString(),
    credits_cost: credits_consumed,
  };
  chatStore.appendMessage(conversation_id, finalMessage);

  // 2. 再清除 streaming 状态（此时 UI 已有正式消息，不会闪烁）
  runtimeStore.clearStreaming(conversation_id);

  // 3. 最后删除乐观消息
  runtimeStore.removeOptimisticMessage(conversation_id, streamingMessageId);

  // 4. 清理订阅
  ws.unsubscribeTask(task_id);
});
```

### 4.6 React Strict Mode 双重渲染

```typescript
// 使用 restorationInProgress 标记防止重复恢复
startRestoration: () => {
  if (state.restorationComplete || state.restorationInProgress) {
    return false; // 阻止重复
  }
  set({ restorationInProgress: true });
  return true;
}
```

### 4.7 WebSocket 与 Hydrate 时序变化

无论哪个先完成，协调器都等待两者都就绪后才触发恢复。

```
场景 A：WS 先连接
t=50ms   WebSocket 连接 → setWsConnected(true) → 等待 hydrate
t=100ms  Hydrate 完成 → setHydrateComplete() → 触发恢复

场景 B：Hydrate 先完成
t=100ms  Hydrate 完成 → setHydrateComplete() → 等待 ws
t=150ms  WebSocket 连接 → setWsConnected(true) → 触发恢复
```

### 4.8 网络断开重连

```typescript
// WebSocket 重连时需要重置恢复状态并重新触发
const handleReconnect = () => {
  const { reset, setWsConnected, setHydrateComplete } = useTaskRestorationStore.getState();

  // 重置状态
  reset();

  // 由于 hydrate 已完成（不会重新执行），直接标记
  setHydrateComplete();

  // WS 连接成功后会触发 setWsConnected(true)，届时会重新恢复
};
```

---

## 5. 迁移计划

### 5.1 实现顺序

1. **Phase 1**: 创建 `useTaskRestorationStore.ts`
2. **Phase 2**: 修改 `taskRestoration.ts`，添加 `initializeTaskRestoration`
3. **Phase 3**: 修改 `useConversationRuntimeStore.ts` 的 `onRehydrateStorage`
4. **Phase 4**: 修改 `WebSocketContext.tsx` 集成统一入口
5. **Phase 5**: 测试所有边界情况

### 5.2 回退方案

如果新方案出现问题，可以通过以下方式回退：

1. 还原 `onRehydrateStorage` 的原有逻辑
2. 还原 `WebSocketContext.tsx` 的 `syncPendingTasks` 调用
3. 保留 `useTaskRestorationStore` 但不使用

### 5.3 测试用例

| 用例 | 步骤 | 预期结果 |
|------|------|----------|
| 正常刷新 | 聊天进行中刷新 | 占位符恢复，继续流式输出 |
| 快速刷新 | 发送后立即刷新 | 用户消息 + 占位符恢复 |
| 刷新中完成 | 刷新过程中任务完成 | 直接显示完成内容 |
| 刷新中失败 | 刷新过程中任务失败 | 显示红色错误消息 |
| 多标签页同步 | 标签 A 完成任务，观察标签 B | B 自动停止 streaming，显示完成内容 |
| API 请求失败 | 模拟 /tasks/pending 超时 | 保留乐观消息，不清空 |
| 网络断开重连 | 断网 10 秒后重连 | 恢复流程重新执行，断点续传 |
| 消息空窗期 | 快速刷新 + 高速输出 | accumulated 确保无丢失 |
| 操作顺序 | chat_done 时观察 UI | 无闪烁，平滑过渡 |

### 5.4 代码清理（实现完成后执行）

实现完成并测试通过后，需要排查和删除以下旧代码/冗余逻辑：

#### 5.4.1 待删除的旧代码

| 文件 | 待删除内容 | 说明 |
|------|-----------|------|
| `WebSocketContext.tsx` | `syncPendingTasks()` 函数 | 已被 `initializeTaskRestoration` 替代 |
| `WebSocketContext.tsx` | `syncPendingTasks` 的 `useEffect` 调用 | 恢复逻辑已统一 |
| `useConversationRuntimeStore.ts` | `onRehydrateStorage` 中的清理逻辑 | 已移至统一入口 |
| `taskRestoration.ts` | 旧的分散式恢复函数（如有） | 统一到 `initializeTaskRestoration` |

#### 5.4.2 需排查的冗余逻辑

| 排查项 | 检查内容 | 处理方式 |
|--------|----------|----------|
| 重复的任务恢复调用 | 搜索 `fetchPendingTasks` 的所有调用点 | 确保只在统一入口调用 |
| 重复的乐观消息清理 | 搜索 `optimisticMessages = []` 的赋值 | 确保只在统一入口清理 |
| 重复的 streaming 状态管理 | 搜索 `setStreamingMessageId` 调用 | 确认恢复场景只在统一入口调用 |
| 未使用的 import | 检查移除旧代码后的 import 语句 | 删除未使用的 import |
| 未使用的类型定义 | 检查 `types/` 目录中的旧类型 | 删除或合并到新类型 |

#### 5.4.3 代码搜索命令

```bash
# 搜索可能的重复恢复逻辑
grep -rn "syncPendingTasks" frontend/src/
grep -rn "fetchPendingTasks" frontend/src/
grep -rn "optimisticMessages\s*=" frontend/src/
grep -rn "restoreTask" frontend/src/

# 搜索旧的清理逻辑
grep -rn "onRehydrateStorage" frontend/src/
grep -rn "clearOptimistic" frontend/src/

# 检查未使用的 export
# 使用 IDE 的 "Find Unused Exports" 功能
```

#### 5.4.4 清理检查清单

完成实现后，逐项确认：

- [ ] `syncPendingTasks` 已删除，无残留调用
- [ ] `onRehydrateStorage` 已简化，只保留 `setHydrateComplete()` 调用
- [ ] `initializeTaskRestoration` 是唯一的恢复入口
- [ ] 无重复的 `fetchPendingTasks` 调用
- [ ] 无重复的乐观消息清理逻辑
- [ ] 所有 import 语句都被使用
- [ ] 无 TODO / FIXME 残留（与本次修改相关的）
- [ ] 无注释掉的旧代码
- [ ] TypeScript 编译无 warning
- [ ] ESLint 检查通过

---

## 6. 总结

本方案通过引入 **TaskRestorationStore** 作为状态协调器，实现了：

1. **统一入口**: 所有任务恢复逻辑集中在 `initializeTaskRestoration`
2. **时序控制**: 明确等待 hydrate + WebSocket 两个条件
3. **条件清理**: 按任务状态决定是否清理乐观消息
4. **架构兼容**: 与 WebSocket 实时推送架构完美配合

### 6.1 关键边界场景处理

| 边界场景 | 处理方案 |
|----------|----------|
| API 请求失败 | 返回 `null` 区分失败与空结果，失败时保留乐观消息 |
| 任务终态漏网 | 后端返回"最近 5 分钟内终结的任务"，前端按状态处理 |
| 多标签页冲突 | WebSocket 广播 + tabSync 双重保障 |
| 消息空窗期 | accumulated_content + 消息缓冲解决 |
| 状态机顺序 | 严格保证：添加正式消息 → 清除 streaming → 删除乐观消息 |

### 6.2 设计评价

相比之前的分散式恢复逻辑，新方案：

- **更清晰**：单一入口，职责明确
- **更健壮**：覆盖所有边界场景
- **更可维护**：协调器模式易于扩展
- **更可测试**：状态机驱动，便于单测
