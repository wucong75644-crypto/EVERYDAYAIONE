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
import { calcRemainingText } from './messageUtils';
import api from '../services/api';
import toast from 'react-hot-toast';
import { logger } from './logger';
import {
  IMAGE_TASK_TIMEOUT,
  VIDEO_TASK_TIMEOUT,
} from '../config/task';
import { getPlaceholderText, type MessageType } from '../constants/placeholder';
import { parseContentParts, parseProtocolString } from '../schemas/messageProtocol';
import { getMessages } from '../services/message';
import { normalizeMessage } from '../stores/useMessageStore';

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
  // WS 订阅用的客户端任务 ID
  client_task_id?: string | null;
  // chat 任务特有字段
  accumulated_content?: unknown;
  accumulated_blocks?: unknown;
  model_id?: string | null;
  error_message?: string | null;
  assistant_message_id?: string | null;
}

/**
 * 获取需要前端恢复/对账的任务
 *
 * 返回值说明：
 * - PendingTask[]: 成功获取，包含活跃、暂停和最近终态任务
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
      // 401 由 api.ts 拦截器统一处理（silentRefresh → 重发 / logoutOnce）
      // 这里不再手动清除 token，只返回 null 表示请求失败
      if (axiosError.response?.status === 401) {
        logger.warn('task:fetch', 'Token 无效，由拦截器处理刷新/登出');
        return null;
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
 * v3.0: 占位符已入库模式
 * - 占位符消息已在 generate 时 insert 到 messages 表
 * - 刷新后通过 loadMessages() 自然加载出 pending 状态的占位符
 * - 此函数只需标记强制刷新 + 等待 WS 推送，不再手动构造占位符
 */
export function restoreMediaTask(task: PendingTask) {
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

  // 1. 标记强制刷新，让 loadMessages 跳过缓存从 API 加载（含 DB 中的占位符）
  store.markForceRefresh(task.conversation_id);

  // 2. 同时添加占位符到 Store（防止 loadMessages 先执行时用了旧缓存）
  //    addMessage 有 ID 去重，loadMessages 从 API 加载后不会重复
  const placeholderId = task.placeholder_message_id || `restored-${task.external_task_id}`;
  const renderHints = task.request_params?._render as Record<string, string> | undefined;
  const loadingText = renderHints?.placeholder_text || getPlaceholderText(task.type as MessageType);

  store.addMessage(task.conversation_id, {
    id: placeholderId,
    conversation_id: task.conversation_id,
    role: 'assistant' as const,
    content: [{ type: 'text' as const, text: loadingText }],
    status: 'pending' as const,
    created_at: task.placeholder_created_at || new Date().toISOString(),
    generation_params: {
      type: task.type,
      model: task.request_params?.model,
      ...(task.request_params?.num_images ? { num_images: task.request_params.num_images } : {}),
      ...(renderHints ? { _render: renderHints } : {}),
    },
  });

  logger.info('task:restore', '媒体任务已恢复，等待 WebSocket 推送', {
    taskId: task.external_task_id,
    type: task.type,
    conversationId: task.conversation_id,
  });
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

    // 2. 先用数据库终态对账 paused/terminal 任务。
    //    markForceRefresh 只是标记，不能替代实际加载；这里直接回读 messages，
    //    确保刷新后不会因为错过 message_done 而残留 streaming/thinking。
    await reconcileChatTaskStates(tasks);

    // 3. 分类任务
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

    // 4. 创建聊天任务占位符（不订阅 WS）
    for (const task of chatTasks) {
      createChatPlaceholder(task);
    }

    // 5. 恢复媒体任务
    for (const task of mediaTasks) {
      try {
        restoreMediaTask(task);
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

const ACTIVE_TASK_STATUSES = new Set(['pending', 'running']);
const RECONCILE_TASK_STATUSES = new Set(['paused', 'completed', 'failed', 'cancelled']);

/**
 * 用后端任务状态校正前端聊天状态。
 *
 * 这是刷新和 WS 重连共同使用的终态对账入口：
 * - active：保留 streaming，等待/继续订阅 WS
 * - paused：清理 streaming，回读已保存的 interrupted partial
 * - terminal：清理 streaming，回读数据库最终消息
 *
 * 同一对话如果同时存在旧 terminal task 和新 active task，以 active 为准，
 * 避免旧任务的对账清掉新一轮生成。
 */
export async function reconcileChatTaskStates(
  providedTasks?: PendingTask[] | null,
): Promise<PendingTask[] | null> {
  const tasks = providedTasks === undefined ? await fetchPendingTasks() : providedTasks;
  if (!tasks) return null;

  const activeConversations = new Set(
    tasks
      .filter((task) => task.type === 'chat' && ACTIVE_TASK_STATUSES.has(task.status))
      .map((task) => task.conversation_id)
      .filter(Boolean),
  );
  const conversations = new Set<string>();

  for (const task of tasks) {
    if (
      task.type !== 'chat'
      || !task.conversation_id
      || !RECONCILE_TASK_STATUSES.has(task.status)
      || activeConversations.has(task.conversation_id)
    ) {
      continue;
    }
    conversations.add(task.conversation_id);
  }

  if (conversations.size === 0) return tasks;

  const store = useMessageStore.getState();
  await Promise.all([...conversations].map(async (conversationId) => {
    store.clearConversationStreaming(conversationId);
    store.markForceRefresh(conversationId);

    try {
      const response = await getMessages(conversationId, 30, 0);
      if (!response?.messages) return;

      const messagesAsc = [...response.messages].map(normalizeMessage).reverse();
      store.setMessagesForConversation(
        conversationId,
        messagesAsc,
        response.messages.length >= 30,
      );
      logger.info('task:reconcile', '聊天任务状态已与数据库对账', {
        conversationId,
        messageCount: messagesAsc.length,
      });
    } catch (error) {
      // 保留 forceRefresh，让当前对话下次加载时继续走 API；不能把网络失败
      // 当成任务终态，也不能用空数据覆盖已有 partial。
      logger.error('task:reconcile', '聊天任务消息对账失败', error, { conversationId });
    }
  }));

  return tasks;
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

  // 任务已入库 = 已路由完成，必须设置 type 以跳过旋转圆点（Phase 1）
  const generationParams = {
    type: 'chat' as const,
    ...(task.model_id ? { model: task.model_id } : {}),
  };

  // 标记强制刷新，让 loadMessages 跳过旧缓存拉取最新数据
  store.markForceRefresh(task.conversation_id);

  // 创建 streaming 占位符（幂等）
  store.startStreaming(task.conversation_id, streamingId, { generationParams });

  // 如果有累积内容，立即显示
  const accumulatedContent = parseProtocolString(
    task.accumulated_content,
    'accumulated_content',
    { source: 'task:restore', conversationId: task.conversation_id },
  );
  const accumulatedBlocks = task.accumulated_blocks === undefined || task.accumulated_blocks === null
    ? []
    : parseContentParts(task.accumulated_blocks, {
      source: 'task:restore',
      conversationId: task.conversation_id,
    });
  if (accumulatedBlocks.length > 0) {
    const remaining = calcRemainingText(accumulatedBlocks, accumulatedContent);
    store.restoreStreamingBlocks(task.conversation_id, accumulatedBlocks, remaining);
    logger.debug('task:restore:p1', '设置累积 blocks', {
      taskId: task.id,
      blocksCount: accumulatedBlocks.length,
      remainingLen: remaining.length,
    });
  } else if (accumulatedContent) {
    store.setStreamingContent(task.conversation_id, accumulatedContent);
    logger.debug('task:restore:p1', '设置累积内容', {
      taskId: task.id,
      contentLen: accumulatedContent.length,
    });
  }

  logger.info('task:restore:p1', '聊天占位符已创建', {
    taskId: task.id,
    streamingId,
    hasContent: !!(accumulatedContent || accumulatedBlocks.length),
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
  // 订阅 chat 任务（优先用 client_task_id，与后端推送 ID 一致）
  for (const task of result.chatTasks) {
    if (task.conversation_id) {
      const subscribeId = task.client_task_id || task.external_task_id;
      subscribeToTask(subscribeId, task.conversation_id);
      logger.info('task:restore:p2', 'Chat 任务已订阅 WS', {
        taskId: subscribeId,
        conversationId: task.conversation_id,
      });
    }
  }

  // 订阅 media 任务（优先用 client_task_id，与后端推送 ID 一致）
  for (const task of result.mediaTasks) {
    if (task.conversation_id) {
      const subscribeId = task.client_task_id || task.external_task_id;
      subscribeToTask(subscribeId, task.conversation_id);
      logger.info('task:restore:p2', 'Media 任务已订阅 WS', {
        taskId: subscribeId,
        conversationId: task.conversation_id,
      });
    }
  }
}
