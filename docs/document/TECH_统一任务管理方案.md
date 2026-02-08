# 统一任务管理方案

> **版本**: v1.0
> **日期**: 2026-02-07
> **状态**: 设计中

## 1. 背景

### 1.1 当前架构问题

```
当前状态：两套独立系统
┌──────────────────────────────────────────────────────────────┐
│  Chat Tasks                    Media Tasks                   │
│  ┌────────────────────┐       ┌────────────────────┐        │
│  │ useTaskStore       │       │ useTaskStore       │        │
│  │   .chatTasks       │       │   .mediaTasks      │        │
│  └────────────────────┘       └────────────────────┘        │
│           ↓                            ↓                     │
│  ┌────────────────────┐       ┌────────────────────┐        │
│  │ RuntimeStore       │       │ RuntimeStore       │        │
│  │ .streamingContent  │       │ .optimisticMessages│        │
│  └────────────────────┘       └────────────────────┘        │
│           ↓                            ↓                     │
│  chat_start/chunk/done        task_status (completed)       │
└──────────────────────────────────────────────────────────────┘
```

**问题**：
1. 两套数据结构，增加维护复杂度
2. 恢复逻辑需要分别处理
3. WebSocket 处理逻辑分散
4. 状态同步容易出错（如刚修复的媒体任务注册遗漏）

### 1.2 目标

```
目标状态：统一任务系统
┌──────────────────────────────────────────────────────────────┐
│                    Unified Task System                       │
│  ┌────────────────────────────────────────────────────────┐ │
│  │              useUnifiedTaskStore                        │ │
│  │                 .tasks: Map<taskId, UnifiedTask>        │ │
│  └────────────────────────────────────────────────────────┘ │
│                            ↓                                 │
│  ┌────────────────────────────────────────────────────────┐ │
│  │              RuntimeStore (保持不变)                    │ │
│  │  .optimisticMessages  .streamingContent                 │ │
│  └────────────────────────────────────────────────────────┘ │
│                            ↓                                 │
│            统一 WebSocket 处理：task_* 事件                  │
└──────────────────────────────────────────────────────────────┘
```

## 2. 数据模型设计

### 2.1 统一任务接口

```typescript
// types/task.ts

/** 任务类型 */
export type TaskType = 'chat' | 'image' | 'video';

/** 任务状态 */
export type TaskStatus = 'pending' | 'running' | 'completed' | 'failed';

/** 统一任务接口 */
export interface UnifiedTask {
  // === 核心标识 ===
  taskId: string;
  conversationId: string;
  type: TaskType;

  // === 状态 ===
  status: TaskStatus;
  startTime: number;

  // === UI 关联 ===
  /** 占位符消息 ID（格式：streaming-{taskId}） */
  placeholderId: string;

  // === 通知相关 ===
  conversationTitle: string;

  // === 聊天专用（可选） ===
  /** 累积的流式内容 */
  accumulatedContent?: string;
  /** 预分配的 assistant 消息 ID */
  assistantMessageId?: string;
}
```

### 2.2 Store 接口

```typescript
// stores/useUnifiedTaskStore.ts

interface UnifiedTaskState {
  tasks: Map<string, UnifiedTask>;
  pendingNotifications: CompletedNotification[];
  recentlyCompleted: Set<string>;

  // === 任务操作 ===
  startTask: (task: Omit<UnifiedTask, 'status' | 'startTime'>) => void;
  updateTask: (taskId: string, updates: Partial<UnifiedTask>) => void;
  completeTask: (taskId: string) => void;
  failTask: (taskId: string) => void;
  removeTask: (taskId: string) => void;

  // === 查询方法 ===
  getTask: (taskId: string) => UnifiedTask | undefined;
  getTasksByConversation: (conversationId: string) => UnifiedTask[];
  hasActiveTask: (conversationId: string) => boolean;
  canStartTask: () => { allowed: boolean; reason?: string };

  // === 通知操作 ===
  markNotificationRead: (id: string) => void;
  clearAllNotifications: () => void;
}
```

## 3. 后端 WebSocket 事件统一

### 3.1 当前事件类型

```
Chat:  chat_start → chat_chunk → chat_done / chat_error
Media: task_status (status: pending/running/completed/failed)
```

### 3.2 统一事件设计

保持后端不变，前端统一处理：

```typescript
// 统一事件处理映射
const EVENT_HANDLERS = {
  // Chat 事件 → 统一处理
  'chat_start': handleTaskStart,
  'chat_chunk': handleTaskProgress,
  'chat_done': handleTaskComplete,
  'chat_error': handleTaskError,

  // Media 事件 → 统一处理
  'task_status': (msg) => {
    if (msg.status === 'running') handleTaskStart(msg);
    if (msg.status === 'completed') handleTaskComplete(msg);
    if (msg.status === 'failed') handleTaskError(msg);
  },
};
```

## 4. 实现方案

### 4.1 新增 useUnifiedTaskStore

```typescript
// stores/useUnifiedTaskStore.ts

import { create } from 'zustand';
import { persist } from 'zustand/middleware';
import { notifyTaskComplete } from '../utils/taskNotification';
import type { UnifiedTask, TaskType, TaskStatus, CompletedNotification } from '../types/task';

const GLOBAL_TASK_LIMIT = 15;

interface UnifiedTaskState {
  tasks: Map<string, UnifiedTask>;
  pendingNotifications: CompletedNotification[];
  recentlyCompleted: Set<string>;

  // 任务操作
  startTask: (params: {
    taskId: string;
    conversationId: string;
    conversationTitle: string;
    type: TaskType;
    placeholderId: string;
    assistantMessageId?: string;
  }) => void;

  updateTaskContent: (taskId: string, content: string) => void;
  completeTask: (taskId: string) => void;
  failTask: (taskId: string) => void;
  removeTask: (taskId: string) => void;

  // 查询方法
  getTask: (taskId: string) => UnifiedTask | undefined;
  getTasksByConversation: (conversationId: string) => UnifiedTask[];
  getTasksByType: (type: TaskType) => UnifiedTask[];
  hasActiveTask: (conversationId: string) => boolean;
  canStartTask: () => { allowed: boolean; reason?: string };

  // 通知操作
  markNotificationRead: (id: string) => void;
  clearRecentlyCompleted: (conversationId: string) => void;
  clearAllNotifications: () => void;
}

export const useUnifiedTaskStore = create<UnifiedTaskState>()(
  persist(
    (set, get) => ({
      tasks: new Map(),
      pendingNotifications: [],
      recentlyCompleted: new Set(),

      startTask: ({ taskId, conversationId, conversationTitle, type, placeholderId, assistantMessageId }) => {
        set((state) => {
          const newTasks = new Map(state.tasks);
          newTasks.set(taskId, {
            taskId,
            conversationId,
            conversationTitle,
            type,
            status: 'pending',
            startTime: Date.now(),
            placeholderId,
            assistantMessageId,
            accumulatedContent: '',
          });
          return { tasks: newTasks };
        });
      },

      updateTaskContent: (taskId: string, content: string) => {
        set((state) => {
          const task = state.tasks.get(taskId);
          if (!task) return state;

          const newTasks = new Map(state.tasks);
          newTasks.set(taskId, {
            ...task,
            status: 'running',
            accumulatedContent: (task.accumulatedContent || '') + content,
          });
          return { tasks: newTasks };
        });
      },

      completeTask: (taskId: string) => {
        const task = get().tasks.get(taskId);
        if (!task) return;

        set((state) => {
          const newTasks = new Map(state.tasks);
          newTasks.delete(taskId);

          const { pendingNotifications, recentlyCompleted } = notifyTaskComplete(
            {
              id: taskId,
              conversationId: task.conversationId,
              conversationTitle: task.conversationTitle,
              type: task.type,
            },
            state.pendingNotifications,
            state.recentlyCompleted
          );

          return { tasks: newTasks, pendingNotifications, recentlyCompleted };
        });
      },

      failTask: (taskId: string) => {
        set((state) => {
          const task = state.tasks.get(taskId);
          if (!task) return state;

          const newTasks = new Map(state.tasks);
          newTasks.set(taskId, { ...task, status: 'failed' });
          return { tasks: newTasks };
        });

        // 3秒后移除
        setTimeout(() => get().removeTask(taskId), 3000);
      },

      removeTask: (taskId: string) => {
        set((state) => {
          const newTasks = new Map(state.tasks);
          newTasks.delete(taskId);
          return { tasks: newTasks };
        });
      },

      getTask: (taskId: string) => get().tasks.get(taskId),

      getTasksByConversation: (conversationId: string) => {
        const tasks: UnifiedTask[] = [];
        for (const task of get().tasks.values()) {
          if (task.conversationId === conversationId) {
            tasks.push(task);
          }
        }
        return tasks;
      },

      getTasksByType: (type: TaskType) => {
        const tasks: UnifiedTask[] = [];
        for (const task of get().tasks.values()) {
          if (task.type === type) {
            tasks.push(task);
          }
        }
        return tasks;
      },

      hasActiveTask: (conversationId: string) => {
        for (const task of get().tasks.values()) {
          if (task.conversationId === conversationId) {
            return true;
          }
        }
        return false;
      },

      canStartTask: () => {
        const totalCount = get().tasks.size;
        if (totalCount >= GLOBAL_TASK_LIMIT) {
          return {
            allowed: false,
            reason: `任务队列已满，最多同时执行 ${GLOBAL_TASK_LIMIT} 个任务`,
          };
        }
        return { allowed: true };
      },

      markNotificationRead: (id: string) => {
        set((state) => ({
          pendingNotifications: state.pendingNotifications.map((n) =>
            n.id === id ? { ...n, isRead: true } : n
          ),
        }));
      },

      clearRecentlyCompleted: (conversationId: string) => {
        set((state) => {
          const newRecentlyCompleted = new Set(state.recentlyCompleted);
          newRecentlyCompleted.delete(conversationId);
          return { recentlyCompleted: newRecentlyCompleted };
        });
      },

      clearAllNotifications: () => set({ pendingNotifications: [] }),
    }),
    {
      name: 'unified-task-store',
      partialize: (state) => ({
        // 只持久化任务列表，用于页面刷新恢复
        tasks: Array.from(state.tasks.entries()),
      }),
      merge: (persisted, current) => ({
        ...current,
        tasks: new Map((persisted as { tasks: [string, UnifiedTask][] })?.tasks || []),
      }),
    }
  )
);
```

### 4.2 WebSocket 统一处理

```typescript
// contexts/WebSocketContext.tsx（修改）

// 统一任务事件处理
useEffect(() => {
  const taskStore = useUnifiedTaskStore.getState();
  const runtimeStore = useConversationRuntimeStore.getState();
  const chatStore = useChatStore.getState();

  // === Chat 事件处理 ===
  const unsubChatStart = ws.subscribe('chat_start', (msg) => {
    const task = taskStore.getTask(msg.task_id);
    if (task) {
      taskStore.updateTask(msg.task_id, { status: 'running' });
    }
    // ... 现有 streaming 逻辑
  });

  const unsubChatChunk = ws.subscribe('chat_chunk', (msg) => {
    const { text } = msg.payload;
    const task = taskStore.getTask(msg.task_id);

    if (task) {
      // 更新任务累积内容
      taskStore.updateTaskContent(msg.task_id, text);
    }
    // ... 现有 RuntimeStore 更新逻辑
  });

  const unsubChatDone = ws.subscribe('chat_done', (msg) => {
    const task = taskStore.getTask(msg.task_id);

    // 1. 完成 UI 更新
    // ... 现有逻辑 ...

    // 2. 完成任务（统一入口）
    if (task) {
      taskStore.completeTask(msg.task_id);
    }
  });

  const unsubChatError = ws.subscribe('chat_error', (msg) => {
    const task = taskStore.getTask(msg.task_id);

    // 1. 错误 UI 处理
    // ... 现有逻辑 ...

    // 2. 标记任务失败（统一入口）
    if (task) {
      taskStore.failTask(msg.task_id);
    }
  });

  // === Media 事件处理（统一到 task_status） ===
  const unsubTaskStatus = ws.subscribe('task_status', async (msg) => {
    const { status } = msg.payload;
    const task = taskStore.getTask(msg.task_id);

    if (!task) return;

    if (status === 'completed') {
      // 1. 移除占位符
      runtimeStore.removeOptimisticMessage(task.conversationId, task.placeholderId);

      // 2. 清缓存触发重新加载
      chatStore.clearConversationCache(task.conversationId);
      chatStore.markConversationUnread(task.conversationId);

      // 3. 完成任务（统一入口）
      taskStore.completeTask(msg.task_id);

      // 4. Toast 提示
      const mediaName = task.type === 'image' ? '图片' : '视频';
      toast.success(`${mediaName}生成完成`);

    } else if (status === 'failed') {
      // ... 错误处理
      taskStore.failTask(msg.task_id);
    }
  });

  return () => { /* cleanup */ };
}, [ws]);
```

### 4.3 统一发送器适配

```typescript
// services/messageSender/unifiedSender.ts（修改）

import { useUnifiedTaskStore } from '../../stores/useUnifiedTaskStore';

// Phase 4.5: 注册任务（统一入口，不再区分 chat/media）
const taskStore = useUnifiedTaskStore.getState();
taskStore.startTask({
  taskId: response.taskId,
  conversationId,
  conversationTitle: params.conversationTitle || '',
  type,
  placeholderId: `streaming-${response.taskId}`,
  assistantMessageId: response.assistantMessageId || undefined,
});
```

### 4.4 任务恢复统一

```typescript
// utils/taskRestoration.ts（简化）

export async function initializeTaskRestoration(
  subscribeTask: (taskId: string, conversationId: string) => void
) {
  const taskStore = useUnifiedTaskStore.getState();
  const tasks = Array.from(taskStore.tasks.values());

  // 统一恢复所有进行中的任务
  for (const task of tasks) {
    if (task.status === 'pending' || task.status === 'running') {
      // 恢复占位符
      restorePlaceholder(task);

      // 订阅 WebSocket
      subscribeTask(task.taskId, task.conversationId);

      logger.info('task:restore', 'restored task', {
        taskId: task.taskId,
        type: task.type,
      });
    }
  }
}

function restorePlaceholder(task: UnifiedTask) {
  const runtimeStore = useConversationRuntimeStore.getState();

  if (task.type === 'chat' && task.accumulatedContent) {
    // 聊天任务：恢复流式状态和累积内容
    runtimeStore.startStreaming(task.conversationId, task.taskId, {
      initialContent: task.accumulatedContent,
    });
  } else {
    // 媒体任务：恢复占位符
    const initialContent = task.type === 'image' ? '图片生成中...' : '视频生成中...';
    runtimeStore.startStreaming(task.conversationId, task.taskId, {
      initialContent,
    });
  }
}
```

## 5. 迁移计划

### Phase 1: 新增统一 Store（保留旧 Store）
1. 创建 `useUnifiedTaskStore`
2. 在 `unifiedSender.ts` 中同时写入两个 Store
3. 验证功能正常

### Phase 2: WebSocket 处理迁移
1. WebSocketContext 改用 `useUnifiedTaskStore`
2. 移除对旧 Store 的依赖
3. 验证 chat/image/video 任务流程

### Phase 3: 任务恢复迁移
1. 统一 `taskRestoration.ts` 逻辑
2. 移除旧的分类恢复代码
3. 验证页面刷新恢复

### Phase 4: 清理旧代码
1. 删除 `useTaskStore` 中的 `chatTasks` 和 `mediaTasks`
2. 删除 `useTaskRestorationStore`（如果不再需要）
3. 更新相关文档

## 6. 验收标准

- [ ] Chat 任务：发送 → 流式 → 完成/失败
- [ ] Image 任务：发送 → 等待 → 完成/失败
- [ ] Video 任务：发送 → 等待 → 完成/失败
- [ ] 页面刷新后所有类型任务正确恢复
- [ ] 通知系统正常工作
- [ ] 任务数量限制生效
- [ ] 无内存泄漏（任务完成后正确清理）

## 7. 收益总结

| 指标 | 当前 | 统一后 |
|------|------|--------|
| Store 数量 | 2 (chatTasks + mediaTasks) | 1 (tasks) |
| 任务注册入口 | 2 (startTask + startMediaTask) | 1 (startTask) |
| WebSocket 处理路径 | 2 (chat_* + task_status) | 统一逻辑 |
| 恢复代码行数 | ~200 行 | ~80 行 |
| 新增任务类型改动点 | 4+ 处 | 1 处 |
