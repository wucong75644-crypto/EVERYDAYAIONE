/**
 * 任务恢复工具 v4.0 - 两阶段架构
 *
 * 设计原则：切换对话（内存秒显） vs 刷新页面（API 加载）分离
 *
 * Phase 1（纯 HTTP，不等 WS）：
 * - hydrate 完成后立即执行
 * - fetch /tasks/pending → 创建占位符/恢复内容
 * - 与消息加载协调：骨架屏等两者都完成才消失
 *
 * Phase 2（WS 就绪后）：
 * - WS 连接成功后执行
 * - 对 Phase 1 中的 running 任务，subscribe 到 WS task channel
 * - 开始接收后续 chunk
 *
 * 任务类型处理：
 * - 聊天任务：Phase 1 创建占位符/恢复内容，Phase 2 订阅 WS
 * - 图片/视频：Phase 1 创建占位符，Phase 2 订阅 WS
 */

import { useMessageStore } from '../stores/useMessageStore';
import api from '../services/api';
import toast from 'react-hot-toast';
import { logger } from './logger';
import { PLACEHOLDER_TEXT } from '../constants/placeholder';
import {
  IMAGE_TASK_TIMEOUT,
  VIDEO_TASK_TIMEOUT,
} from '../config/task';

interface TaskRequestParams {
  prompt?: string;
  model?: string;
  size?: string;
  output_format?: string;
  resolution?: string;
  aspect_ratio?: string;
  n_frames?: string;
  content?: string;
  thinking_effort?: string;
  thinking_mode?: string;
  [key: string]: string | undefined;
}

export interface PendingTask {
  id: string;
  external_task_id: string;
  conversation_id: string;
  type: 'image' | 'video' | 'chat';
  status: string;
  request_params: TaskRequestParams;
  credits_locked: number;
  placeholder_message_id: string | null;
  placeholder_created_at: string | null;
  started_at: string;
  last_polled_at: string | null;
  // chat 任务特有字段
  accumulated_content?: string | null;
  model_id?: string | null;
  error_message?: string | null;
  assistant_message_id?: string | null;
}

/**
 * 获取进行中的任务
 *
 * 返回值说明：
 * - PendingTask[]: 成功获取，可能为空数组
 * - null: 请求失败（网络错误/超时等）
 *
 * 调用方应区分这两种情况：
 * - 空数组：无进行中任务，可以清理乐观消息
 * - null：请求失败，应保留乐观消息
 */
export async function fetchPendingTasks(): Promise<PendingTask[] | null> {
  // 检查是否有有效的 token
  const token = localStorage.getItem('access_token');
  if (!token) {
    logger.info('task:fetch', '未登录，跳过任务恢复');
    return []; // 返回空数组而不是 null，表示没有任务需要恢复
  }

  try {
    const response = await api.get<{ tasks: PendingTask[]; count: number }>('/tasks/pending');
    logger.debug('task:fetch', '获取进行中任务', { count: response.data.tasks?.length ?? 0 });
    return response.data.tasks || [];
  } catch (error) {
    // 提供更详细的错误信息
    if (error && typeof error === 'object' && 'response' in error) {
      const axiosError = error as { response?: { status?: number; data?: unknown } };
      logger.error('task:fetch', '获取进行中任务失败', error, {
        status: axiosError.response?.status,
        data: axiosError.response?.data,
      });
      // 如果是 401 错误，清除 token 并返回空数组（不影响其他功能）
      if (axiosError.response?.status === 401) {
        logger.warn('task:fetch', 'Token 无效，清除登录状态');
        localStorage.removeItem('access_token');
        localStorage.removeItem('user');
        return [];
      }
    } else {
      logger.error('task:fetch', '获取进行中任务失败', error);
    }
    return null; // null 表示请求失败，区别于空数组
  }
}

/**
 * 恢复媒体任务（图片/视频）
 *
 * v2.0: WebSocket 推送模式
 * - 只创建占位符和注册任务到 Store
 * - 不启动轮询，等待 WebSocket message_done/error 事件处理完成
 * - 后端完成后通过 WebSocket 推送 message_done/error 事件
 */
export function restoreMediaTask(task: PendingTask, _conversationTitle: string) {
  const store = useMessageStore.getState();

  const maxDuration = task.type === 'image' ? IMAGE_TASK_TIMEOUT : VIDEO_TASK_TIMEOUT;
  const elapsed = Date.now() - new Date(task.started_at).getTime();

  // 超时检查：已超时的任务不恢复（后端会标记为失败）
  if (elapsed > maxDuration) {
    logger.warn('task:restore', '任务已超时,跳过恢复', { taskId: task.external_task_id });
    return;
  }

  // 验证对话 ID 有效性
  if (!task.conversation_id) {
    logger.warn('task:restore', '任务没有关联对话,跳过恢复', { taskId: task.external_task_id });
    return;
  }

  // 使用数据库中保存的原始占位符 ID（与前端生成的 UUID 一致）
  const placeholderId = task.placeholder_message_id || `restored-${task.external_task_id}`;

  // 1. 创建占位符消息（直接添加到 messages，不再使用 streaming/optimistic）
  const loadingText = task.type === 'image'
    ? PLACEHOLDER_TEXT.IMAGE_GENERATING
    : PLACEHOLDER_TEXT.VIDEO_GENERATING;

  // 使用数据库保存的占位符时间戳（确保与原始占位符时间一致）
  const placeholderTimestamp = task.placeholder_created_at || new Date().toISOString();

  // 警告：如果没有 placeholder_created_at（旧数据），时间戳可能不准确
  if (!task.placeholder_created_at) {
    logger.warn('task:restore', '任务缺少 placeholder_created_at，使用当前时间', {
      taskId: task.external_task_id,
      fallbackTime: placeholderTimestamp,
    });
  }

  const placeholderMessage = {
    id: placeholderId,
    conversation_id: task.conversation_id,
    role: 'assistant' as const,
    content: [{ type: 'text' as const, text: loadingText }],
    status: 'pending' as const,
    created_at: placeholderTimestamp,
    generation_params: {
      type: task.type,
      model: task.request_params?.model,
    },
  };

  store.addMessage(task.conversation_id, placeholderMessage);

  // 3. 不启动轮询！等待 WebSocket message_done/error 事件
  // 后端完成后会推送 message_done/error 事件，由 WebSocketContext 处理
  logger.info('task:restore', '媒体任务已恢复，等待 WebSocket 推送', {
    taskId: task.external_task_id,
    type: task.type,
    conversationId: task.conversation_id,
  });
}


// 跟踪待处理的恢复任务超时
let pendingRestoreTimeouts: ReturnType<typeof setTimeout>[] = [];

/**
 * 取消所有待处理的任务恢复
 * 用于防止重复恢复（如 React strict mode）
 */
export function cancelPendingRestorations() {
  pendingRestoreTimeouts.forEach(clearTimeout);
  pendingRestoreTimeouts = [];
}


// ============================================================
// Phase 1：纯 HTTP 恢复（不等 WS）
// ============================================================

/** Phase 1 恢复结果，传递给 Phase 2 使用 */
export interface RestorationResult {
  /** 需要 WS 订阅的 chat 任务 */
  chatTasks: PendingTask[];
  /** 需要 WS 订阅的 media 任务 */
  mediaTasks: PendingTask[];
}

/**
 * Phase 1：获取 pending 任务并创建占位符（纯 HTTP，不等 WS）
 *
 * 调用时机：hydrate 完成后立即执行
 * 与消息加载并行，骨架屏等两者都完成才消失
 *
 * 职责：
 * 1. 获取所有进行中的任务
 * 2. 创建占位符 / 恢复部分内容
 * 3. 返回需要 WS 订阅的任务列表（交给 Phase 2）
 */
export async function restoreTaskPlaceholders(): Promise<RestorationResult | null> {
  try {
    // 1. 获取所有进行中的任务
    const tasks = await fetchPendingTasks();

    if (tasks === null) {
      logger.warn('task:restore:p1', 'API 请求失败，跳过恢复');
      return null;
    }

    // 2. 分类任务
    const chatTasks = tasks.filter(
      t => t.type === 'chat' && (t.status === 'pending' || t.status === 'running')
    );
    const mediaTasks = tasks.filter(
      t => (t.type === 'image' || t.type === 'video') && (t.status === 'pending' || t.status === 'running')
    );

    logger.info('task:restore:p1', '获取进行中任务', {
      total: tasks.length,
      chat: chatTasks.length,
      media: mediaTasks.length,
    });

    // 3. 处理已终结的任务（标记强制刷新）
    const terminatedTasks = tasks.filter(
      t => t.status === 'completed' || t.status === 'failed'
    );
    if (terminatedTasks.length > 0) {
      handleTerminatedTasks(terminatedTasks);
    }

    // 4. 创建聊天任务占位符（不订阅 WS）
    for (const task of chatTasks) {
      createChatPlaceholder(task);
    }

    // 5. 创建媒体任务占位符
    const store = useMessageStore.getState();
    const conversationTitles = new Map<string, string>();
    for (const conv of store.conversations) {
      conversationTitles.set(conv.id, conv.title);
    }

    cancelPendingRestorations();
    for (const task of mediaTasks) {
      const title = conversationTitles.get(task.conversation_id) || '进行中的任务';
      try {
        restoreMediaTask(task, title);
      } catch (error) {
        logger.error('task:restore:p1', '恢复媒体任务失败', error, { taskId: task.id });
      }
    }

    // 6. 显示恢复提示
    const totalRestored = chatTasks.length + mediaTasks.length;
    if (totalRestored > 0) {
      toast.success(`正在恢复 ${totalRestored} 个任务`);
    }

    return { chatTasks, mediaTasks };
  } catch (error) {
    logger.error('task:restore:p1', '任务恢复异常', error);
    return null;
  }
}

/**
 * 创建聊天任务占位符（Phase 1 使用，不订阅 WS）
 */
function createChatPlaceholder(task: PendingTask) {
  if (!task.conversation_id) {
    logger.warn('task:restore:p1', '聊天任务没有关联对话', { taskId: task.external_task_id });
    return;
  }

  const store = useMessageStore.getState();
  const messageId = task.placeholder_message_id || task.assistant_message_id;
  const streamingId = messageId || task.id;

  const generationParams = task.model_id
    ? { model: task.model_id }
    : undefined;

  // 创建 streaming 占位符（幂等）
  store.startStreaming(task.conversation_id, streamingId, { generationParams });

  // 如果有累积内容，立即显示
  if (task.accumulated_content) {
    store.setStreamingContent(task.conversation_id, task.accumulated_content);
    logger.debug('task:restore:p1', '设置累积内容', {
      taskId: task.id,
      contentLen: task.accumulated_content.length,
    });
  }

  logger.info('task:restore:p1', '聊天占位符已创建', {
    taskId: task.id,
    streamingId,
    hasContent: !!task.accumulated_content,
  });
}

// ============================================================
// Phase 2：WS 订阅（WS 就绪后执行）
// ============================================================

/**
 * Phase 2：为 Phase 1 中的任务订阅 WS
 *
 * 调用时机：WS 连接成功后
 */
export function subscribeRestoredTasks(
  result: RestorationResult,
  subscribeToTask: (taskId: string, conversationId: string) => void
) {
  // 订阅 chat 任务
  for (const task of result.chatTasks) {
    if (task.conversation_id) {
      subscribeToTask(task.external_task_id, task.conversation_id);
      logger.info('task:restore:p2', 'Chat 任务已订阅 WS', {
        taskId: task.external_task_id,
        conversationId: task.conversation_id,
      });
    }
  }

  // 订阅 media 任务（它们也需要 WS 推送 message_done）
  for (const task of result.mediaTasks) {
    if (task.conversation_id) {
      subscribeToTask(task.external_task_id, task.conversation_id);
      logger.info('task:restore:p2', 'Media 任务已订阅 WS', {
        taskId: task.external_task_id,
        conversationId: task.conversation_id,
      });
    }
  }
}

/**
 * 处理刷新期间已终结的任务（聊天 + 媒体统一处理）
 *
 * 设计原则：只标记，不加载
 * - 标记相关对话需要强制刷新
 * - 实际加载由 loadMessages 统一处理
 * - 避免与 loadMessages 产生竞争条件
 *
 * 触发条件：
 * - /tasks/pending API 返回最近 5 分钟内完成/失败的任务
 * - 这些任务的消息可能不在当前缓存中
 */
function handleTerminatedTasks(tasks: PendingTask[]) {
  if (tasks.length === 0) return;

  const store = useMessageStore.getState();

  for (const task of tasks) {
    if (!task.conversation_id) continue;

    // 标记该对话需要强制刷新（loadMessages 会检查并处理）
    store.markForceRefresh(task.conversation_id);

    logger.info('task:restore', '任务已终结，标记强制刷新', {
      taskId: task.id,
      type: task.type,
      status: task.status,
      conversationId: task.conversation_id,
    });
  }
}
