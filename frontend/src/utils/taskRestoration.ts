/**
 * 任务恢复工具 v3.0 - 统一入口架构
 *
 * 核心改进：
 * - 统一恢复入口 `initializeTaskRestoration`
 * - 解决 hydrate 与 WebSocket 的竞态条件
 * - 按任务状态条件清理乐观消息
 *
 * 调用时机：
 * - Zustand hydrate 完成 AND WebSocket 连接就绪
 * - 由 TaskRestorationStore 协调触发
 *
 * 任务类型处理：
 * - 聊天任务：创建占位符 + WebSocket 订阅
 * - 图片/视频：创建占位符，等待 WebSocket task_status 推送
 */

import { useMessageStore } from '../stores/useMessageStore';
import { useTaskRestorationStore } from '../stores/useTaskRestorationStore';
import api from '../services/api';
import toast from 'react-hot-toast';
import { logger } from './logger';
import { PLACEHOLDER_TEXT } from '../constants/placeholder';
import {
  IMAGE_TASK_TIMEOUT,
  VIDEO_TASK_TIMEOUT,
  TASK_RESTORE_STAGGER_DELAY,
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
  image_url?: string;
  video_url?: string;
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
 * - 不启动轮询，等待 WebSocket task_status 事件处理完成
 * - 后端完成后通过 WebSocket 推送 task_status 事件
 */
export function restoreMediaTask(task: PendingTask, conversationTitle: string) {
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

  // 确保占位符 ID 有 streaming- 前缀，与正常创建流程一致
  // 注意：startStreaming 会自动添加 streaming- 前缀，所以这里提取基础 ID
  const rawPlaceholderId = task.placeholder_message_id || `restored-${task.external_task_id}`;
  const streamingIdBase = rawPlaceholderId.replace(/^streaming-/, '');
  const placeholderId = `streaming-${streamingIdBase}`;

  // 1. 注册任务到 Store（用于 UI 显示状态）
  store.startMediaTask({
    taskId: task.external_task_id,
    conversationId: task.conversation_id,
    conversationTitle,
    type: task.type as 'image' | 'video',
    placeholderId,
  });

  // 2. 统一使用 startStreaming 创建占位符（与正常发送流程一致）
  const loadingText = task.type === 'image'
    ? PLACEHOLDER_TEXT.IMAGE_GENERATING
    : PLACEHOLDER_TEXT.VIDEO_GENERATING;
  const placeholderTimestamp = task.placeholder_created_at || new Date().toISOString();
  store.startStreaming(task.conversation_id, streamingIdBase, {
    initialContent: loadingText,
    createdAt: placeholderTimestamp,
  });

  // 3. 不启动轮询！等待 WebSocket task_status 事件
  // 后端完成后会推送 task_status 事件，由 WebSocketContext 处理
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
// 统一任务恢复入口（v3.0）
// ============================================================

/**
 * 统一任务恢复入口
 *
 * 调用时机：hydrate 完成 AND WebSocket 连接就绪
 * 由 TaskRestorationStore 协调触发
 *
 * 职责：
 * 1. 获取所有进行中的任务
 * 2. 按任务状态条件清理乐观消息
 * 3. 恢复聊天任务（创建占位符 + WebSocket 订阅）
 * 4. 恢复媒体任务（创建占位符）
 */
export async function initializeTaskRestoration(
  subscribeToTask: (taskId: string, conversationId: string) => void
): Promise<void> {
  const { startRestoration, completeRestoration } = useTaskRestorationStore.getState();

  // 检查是否可以开始恢复
  if (!startRestoration()) {
    return;
  }

  try {
    // 1. 获取所有进行中的任务
    const tasks = await fetchPendingTasks();

    // 2. 处理 API 请求失败的情况
    if (tasks === null) {
      logger.warn('task:restore', 'API 请求失败，跳过恢复');
      return;
    }

    // 3. 分类任务（只处理进行中的任务）
    const chatTasks = tasks.filter(
      t => t.type === 'chat' && (t.status === 'pending' || t.status === 'running')
    );
    const mediaTasks = tasks.filter(
      t => (t.type === 'image' || t.type === 'video') && (t.status === 'pending' || t.status === 'running')
    );

    logger.info('task:restore', '获取进行中任务', {
      total: tasks.length,
      chat: chatTasks.length,
      media: mediaTasks.length,
    });

    // 4. 处理已终结的任务（刷新期间完成/失败的，只标记不加载）
    const terminatedTasks = tasks.filter(
      t => t.status === 'completed' || t.status === 'failed'
    );
    if (terminatedTasks.length > 0) {
      handleTerminatedTasks(terminatedTasks);
    }

    // 5. 获取对话标题映射（用于媒体任务）
    const store = useMessageStore.getState();
    const conversationTitles = new Map<string, string>();
    for (const conv of store.conversations) {
      conversationTitles.set(conv.id, conv.title);
    }

    // 6. 恢复聊天任务
    for (const task of chatTasks) {
      restoreChatTask(task, subscribeToTask);
    }

    // 7. 恢复媒体任务（带错开延迟）
    cancelPendingRestorations();

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
    logger.error('task:restore', '任务恢复异常', error);
  } finally {
    completeRestoration();
  }
}

/**
 * 恢复单个聊天任务
 *
 * 1. 检查缓存中是否已有对应的 AI 消息（防御已完成任务）
 * 2. 创建 streaming 占位符
 * 3. 订阅 WebSocket 任务通道
 */
function restoreChatTask(
  task: PendingTask,
  subscribeToTask: (taskId: string, conversationId: string) => void
) {
  if (!task.conversation_id) {
    logger.warn('task:restore', '聊天任务没有关联对话', { taskId: task.external_task_id });
    return;
  }

  const store = useMessageStore.getState();

  // 获取消息 ID（优先使用 placeholder_message_id，chat handler 保存的是这个字段）
  // assistant_message_id 是后加的字段，可能为 null
  const messageId = task.placeholder_message_id || task.assistant_message_id;

  // 防御检查：如果缓存中已有对应的 AI 消息，说明任务已完成，跳过恢复
  // 这处理后端返回的任务状态不准确（应该是 completed 但返回了 pending/running）的情况
  const cachedData = store.getCachedMessages(task.conversation_id);
  if (cachedData?.messages && messageId) {
    const existingMessage = cachedData.messages.find(
      m => m.id === messageId && m.role === 'assistant'
    );
    if (existingMessage) {
      logger.info('task:restore', '缓存中已有对应 AI 消息，跳过恢复', {
        taskId: task.id,
        messageId,
        conversationId: task.conversation_id,
      });
      return;
    }
  }

  // 创建 streaming 占位符
  // 使用消息 ID 作为 streamingId，确保与发送时创建的占位符 ID 一致
  const streamingId = messageId || task.id;

  // 构建 generationParams（用于重新生成时复用模型）
  const generationParams = task.model_id
    ? { model: task.model_id }
    : undefined;

  // 使用 startStreaming 创建占位符（幂等，已存在则不重复创建）
  store.startStreaming(task.conversation_id, streamingId, {
    generationParams,
  });

  // 如果 API 返回了累积内容，立即设置到占位符
  // 这确保刷新后能立即看到已生成的内容，不依赖 WebSocket subscribed 消息
  if (task.accumulated_content) {
    store.setStreamingContent(task.conversation_id, task.accumulated_content);
    logger.debug('task:restore', '设置累积内容', {
      taskId: task.id,
      contentLen: task.accumulated_content.length,
    });
  }

  // 订阅 WebSocket 任务通道（后续增量内容通过 chat_chunk 推送）
  // 注意：必须使用 external_task_id，因为后端 WebSocket 推送使用的是这个 ID
  subscribeToTask(task.external_task_id, task.conversation_id);

  logger.info('task:restore', '聊天任务已恢复', {
    taskId: task.id,
    externalTaskId: task.external_task_id,
    conversationId: task.conversation_id,
    streamingId,
    messageId,
    hasAccumulatedContent: !!task.accumulated_content,
  });
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
