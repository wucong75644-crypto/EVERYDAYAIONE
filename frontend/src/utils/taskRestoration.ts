/**
 * 任务恢复工具
 *
 * 支持：
 * - 图片/视频任务恢复（轮询模式）
 * - 聊天任务恢复（SSE 流式连接恢复）
 * - 多标签页防重复恢复
 * - 幂等性保护
 */

import { useTaskStore } from '../stores/useTaskStore';
import { useChatStore } from '../stores/useChatStore';
import { useConversationRuntimeStore } from '../stores/useConversationRuntimeStore';
import { messageCoordinator } from './messageCoordinator';
import { queryTaskStatus as getImageTaskStatus, type TaskStatusResponse as ImageTaskStatusResponse } from '../services/image';
import { queryVideoTaskStatus as getVideoTaskStatus, type TaskStatusResponse as VideoTaskStatusResponse } from '../services/video';
import { createMessage, getMessages } from '../services/message';
import { createStreamingPlaceholder } from './messageFactory';
import { tabSync } from './tabSync';
import api from '../services/api';
import toast from 'react-hot-toast';
import { logger } from './logger';
import { fetchEventSource } from '@microsoft/fetch-event-source';
import {
  IMAGE_TASK_TIMEOUT,
  VIDEO_TASK_TIMEOUT,
  IMAGE_POLL_INTERVAL,
  VIDEO_POLL_INTERVAL,
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

interface PendingTask {
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

type TaskResult = ImageTaskStatusResponse | VideoTaskStatusResponse;

// 【幂等性保护】活动恢复追踪
const activeRecoveries = new Map<string, AbortController>();

// 【多标签页防重复】被其他标签页恢复的任务 ID 集合
const restoredByOtherTabs = new Set<string>();

// 监听其他标签页的恢复通知
tabSync.on('task_restored', (payload) => {
  if (payload.taskId) {
    restoredByOtherTabs.add(payload.taskId as string);
  }
});

/**
 * 取消所有恢复（页面卸载前调用）
 */
export function cancelAllRecoveries() {
  activeRecoveries.forEach((controller) => controller.abort());
  activeRecoveries.clear();
}

// 页面卸载前取消所有恢复
if (typeof window !== 'undefined') {
  window.addEventListener('beforeunload', cancelAllRecoveries);
}

export async function fetchPendingTasks(): Promise<PendingTask[]> {
  console.log('[TaskRestore] fetchPendingTasks START (v2)');
  try {
    const response = await api.get<{ tasks: PendingTask[]; count: number }>('/tasks/pending');
    console.log('[TaskRestore] fetchPendingTasks:', response.data.tasks?.length, 'tasks');
    response.data.tasks?.forEach(t => {
      console.log('[TaskRestore] pending task:', { id: t.id, type: t.type, status: t.status, conversationId: t.conversation_id });
    });
    return response.data.tasks || [];
  } catch (error) {
    logger.error('task:fetch', '获取进行中任务失败', error);
    return [];
  }
}

/**
 * 恢复聊天任务
 */
async function restoreChatTask(task: PendingTask) {
  const {
    id: taskId,
    conversation_id: conversationId,
    assistant_message_id: assistantMessageId,
    status,
    error_message: errorMessage,
  } = task;

  console.log('[TaskRestore] restoreChatTask called:', { taskId, conversationId, status, assistantMessageId });

  const runtimeStore = useConversationRuntimeStore.getState();

  // 1. 如果任务进行中，创建 streaming 消息占位
  // 【关键】使用预分配的 assistant_message_id，避免 UI 闪烁
  // 【注意】不在这里调用 appendStreamingContent，由 SSE handler 统一负责内容更新，避免内容重复
  if (status === 'running' || status === 'pending') {
    // 【重要】startStreaming 内部会自动添加 streaming- 前缀
    // 所以这里传入原始 ID，不带前缀
    const streamingId = assistantMessageId || taskId;
    console.log('[TaskRestore] calling startStreaming:', { conversationId, streamingId });
    runtimeStore.startStreaming(conversationId, streamingId);
  }

  // 2. 根据状态处理
  if (status === 'running' || status === 'pending') {
    // 恢复 SSE 连接，继续接收流式内容
    console.log('[TaskRestore] calling resumeChatStream');
    await resumeChatStream(taskId, conversationId, assistantMessageId || undefined);
  } else if (status === 'completed') {
    // 已完成，重新加载消息列表
    console.log('[TaskRestore] task completed, reloading messages');
    await reloadConversationMessages(conversationId);
  } else if (status === 'failed') {
    // 失败，显示错误
    const errorMsg = {
      id: assistantMessageId || `error-${taskId}`,
      conversation_id: conversationId,
      role: 'assistant' as const,
      content: errorMessage || '生成失败，请重试',
      created_at: new Date().toISOString(),
      credits_cost: 0,
      is_error: true,
    };
    runtimeStore.addErrorMessage(conversationId, errorMsg);
  }
}

/**
 * 恢复聊天流式连接
 *
 * 使用 @microsoft/fetch-event-source 替代原生 EventSource
 * 原因：EventSource 不支持自定义 headers，无法携带认证 token
 */
async function resumeChatStream(
  taskId: string,
  conversationId: string,
  assistantMessageId?: string,
) {
  // 【多标签页防重复】检查是否已被其他标签页恢复
  if (restoredByOtherTabs.has(taskId)) {
    logger.info('task:restore', `Task ${taskId} already restored by another tab`);
    return;
  }

  // 【幂等性检查】防止重复恢复
  if (activeRecoveries.has(taskId)) {
    logger.info('task:restore', `Recovery already in progress: ${taskId}`);
    return;
  }

  const controller = new AbortController();
  activeRecoveries.set(taskId, controller);

  // 【广播】告诉其他标签页这个任务已被恢复
  tabSync.broadcast('task_restored', {
    taskId,
    conversationId,
  });

  const runtimeStore = useConversationRuntimeStore.getState();
  const chatStore = useChatStore.getState();
  const token = localStorage.getItem('access_token');

  try {
    console.log('[TaskRestore] SSE connecting to:', `/api/tasks/${taskId}/stream`);
    await fetchEventSource(`/api/tasks/${taskId}/stream`, {
      method: 'GET',
      headers: {
        'Authorization': token ? `Bearer ${token}` : '',
        'Accept': 'text/event-stream',
      },
      signal: controller.signal,

      onopen: async (response) => {
        console.log('[TaskRestore] SSE onopen:', response.status, response.ok);
        if (!response.ok) {
          throw new Error(`SSE connection failed: ${response.status}`);
        }
      },

      onmessage: (event) => {
        console.log('[TaskRestore] SSE onmessage:', event.data.substring(0, 100));
        if (event.data === '[DONE]') {
          console.log('[TaskRestore] SSE [DONE] received');
          activeRecoveries.delete(taskId);
          runtimeStore.completeStreaming(conversationId);
          controller.abort(); // 关闭连接
          return;
        }

        try {
          const data = JSON.parse(event.data);
          console.log('[TaskRestore] SSE event type:', data.type);

          if (data.type === 'accumulated') {
            // 累积内容（首次恢复时发送的完整已有内容）
            const streamingId = assistantMessageId || taskId;
            console.log('[TaskRestore] accumulated content length:', data.data.text?.length);
            runtimeStore.startStreaming(conversationId, streamingId);
            runtimeStore.appendStreamingContent(conversationId, data.data.text);
          } else if (data.type === 'content') {
            // 增量内容
            runtimeStore.appendStreamingContent(conversationId, data.data.text);
          } else if (data.type === 'done') {
            // 完成
            runtimeStore.completeStreaming(conversationId);
            if (data.data.assistant_message) {
              chatStore.appendMessage(conversationId, data.data.assistant_message);
              tabSync.broadcast('chat_completed', {
                conversationId,
                messageId: data.data.assistant_message.id,
              });
            }
            activeRecoveries.delete(taskId);
            controller.abort();
          } else if (data.type === 'error') {
            runtimeStore.completeStreaming(conversationId);
            const errorMsg = {
              id: assistantMessageId || `error-${taskId}`,
              conversation_id: conversationId,
              role: 'assistant' as const,
              content: data.data.message,
              created_at: new Date().toISOString(),
              credits_cost: 0,
              is_error: true,
            };
            runtimeStore.addErrorMessage(conversationId, errorMsg);
            activeRecoveries.delete(taskId);
            tabSync.broadcast('chat_failed', { conversationId, taskId });
            controller.abort();
          }
        } catch (parseError) {
          logger.error('task:restore', 'Failed to parse SSE data', { error: String(parseError) });
        }
      },

      onerror: (error) => {
        activeRecoveries.delete(taskId);
        // SSE 失败，降级到轮询模式
        logger.warn('task:restore', 'SSE failed, falling back to polling', { error: String(error) });
        pollTaskStatus(taskId, conversationId, assistantMessageId);
        // 抛出错误阻止自动重连
        throw error;
      },

      onclose: () => {
        activeRecoveries.delete(taskId);
      },
    });
  } catch (error) {
    activeRecoveries.delete(taskId);
    // 如果不是主动中止，则降级到轮询
    if (error instanceof Error && error.name !== 'AbortError') {
      logger.warn('task:restore', 'SSE connection error, falling back to polling', { error: String(error) });
      pollTaskStatus(taskId, conversationId, assistantMessageId);
    }
  }
}

/**
 * 轮询任务状态（SSE 连接失败时的兜底）
 *
 * @param lastContentLength 上次轮询时的内容长度，用于计算增量内容
 */
async function pollTaskStatus(
  taskId: string,
  conversationId: string,
  assistantMessageId?: string,
  lastContentLength: number = 0,
) {
  console.log('[TaskRestore] pollTaskStatus called:', { taskId, conversationId, lastContentLength });
  const runtimeStore = useConversationRuntimeStore.getState();

  // 确保 streaming 消息已创建（幂等操作）
  const streamingId = assistantMessageId || taskId;
  runtimeStore.startStreaming(conversationId, streamingId);

  try {
    const response = await api.get<{
      task_id: string;
      status: string;
      accumulated_content?: string;
      error_message?: string;
      assistant_message_id?: string;
    }>(`/tasks/${taskId}/content`);

    const { status, accumulated_content, error_message } = response.data;
    console.log('[TaskRestore] poll response:', { status, contentLength: accumulated_content?.length });

    if (status === 'completed') {
      console.log('[TaskRestore] task completed via polling');
      runtimeStore.completeStreaming(conversationId);
      // 重新加载消息
      await reloadConversationMessages(conversationId);
    } else if (status === 'failed') {
      runtimeStore.completeStreaming(conversationId);
      const errorMsg = {
        id: assistantMessageId || `error-${taskId}`,
        conversation_id: conversationId,
        role: 'assistant' as const,
        content: error_message || '生成失败，请重试',
        created_at: new Date().toISOString(),
        credits_cost: 0,
        is_error: true,
      };
      runtimeStore.addErrorMessage(conversationId, errorMsg);
    } else if (status === 'running' || status === 'pending') {
      // 任务进行中
      const currentContent = accumulated_content || '';
      const currentLength = currentContent.length;

      // 只追加新增的内容（避免重复）
      if (currentLength > lastContentLength) {
        const newContent = currentContent.slice(lastContentLength);
        runtimeStore.appendStreamingContent(conversationId, newContent);
      }

      // 继续轮询（传入当前内容长度）
      setTimeout(
        () => pollTaskStatus(taskId, conversationId, assistantMessageId, currentLength),
        1000
      );
    }
  } catch (error) {
    logger.error('task:restore', 'Failed to poll task status', error);
    runtimeStore.completeStreaming(conversationId);
  }
}

/**
 * 重新加载对话消息
 */
async function reloadConversationMessages(conversationId: string) {
  const chatStore = useChatStore.getState();

  try {
    const result = await getMessages(conversationId, 50, 0);
    chatStore.setMessagesForConversation(conversationId, result.messages, result.messages.length >= 50);
  } catch (error) {
    logger.error('task:restore', 'Failed to reload messages', error);
  }
}

export function restoreTaskPolling(task: PendingTask, conversationTitle: string) {
  const { startMediaTask, startPolling, completeMediaTask, failMediaTask } =
    useTaskStore.getState();
  const { replaceMediaPlaceholder, addMediaPlaceholder } = useConversationRuntimeStore.getState();
  const { appendMessage } = useChatStore.getState();

  const maxDuration = task.type === 'image' ? IMAGE_TASK_TIMEOUT : VIDEO_TASK_TIMEOUT;
  const elapsed = Date.now() - new Date(task.started_at).getTime();

  if (elapsed > maxDuration) {
    logger.warn('task:restore', '任务已超时,跳过恢复', { taskId: task.external_task_id });
    markTaskAsFailed(task.external_task_id, '任务超时');
    return;
  }

  // 验证对话 ID 有效性（任务可能是在临时对话中创建的，此时 conversation_id 为 null）
  if (!task.conversation_id) {
    logger.warn('task:restore', '任务没有关联对话,跳过恢复', { taskId: task.external_task_id });
    markTaskAsFailed(task.external_task_id, '任务未关联对话');
    return;
  }

  const placeholderId = task.placeholder_message_id ||
    `restored-${task.external_task_id}`;

  startMediaTask({
    taskId: task.external_task_id,
    conversationId: task.conversation_id,
    conversationTitle,
    type: task.type as 'image' | 'video',
    placeholderId,
  });

  // 创建占位符消息并添加到 RuntimeStore，确保刷新后 UI 能显示"生成中"状态
  // 使用原始占位符时间戳（如果存在），确保消息排序正确
  // 如果没有原始时间戳（旧任务），则使用当前时间戳
  const loadingText = task.type === 'image' ? '图片生成中...' : '视频生成中...';
  const placeholderTimestamp = task.placeholder_created_at || new Date().toISOString();
  const placeholder = createStreamingPlaceholder(
    task.conversation_id,
    placeholderId,
    loadingText,
    placeholderTimestamp
  );
  addMediaPlaceholder(task.conversation_id, placeholder);

  const pollFn = task.type === 'image'
    ? getImageTaskStatus
    : getVideoTaskStatus;

  const pollInterval = task.type === 'image' ? IMAGE_POLL_INTERVAL : VIDEO_POLL_INTERVAL;
  const remainingTime = maxDuration - elapsed;

  startPolling(
    task.external_task_id,
    async () => {
      const result = await pollFn(task.external_task_id);
      if (result.status === 'success') return { done: true, result };
      if (result.status === 'failed') return { done: true, error: new Error(result.fail_msg || '任务失败') };
      return { done: false };
    },
    {
      onSuccess: async (result: unknown) => {
        try {
          const taskResult = result as TaskResult;
          const mediaUrl = task.type === 'image' ? (taskResult as ImageTaskStatusResponse).image_urls[0] : (taskResult as VideoTaskStatusResponse).video_url;
          const successContent = task.type === 'image' ? '图片已生成完成' : '视频生成完成';

          // 检查是否已存在相同媒体 URL 的消息（防止刷新后重复创建）
          try {
            const existingMessages = await getMessages(task.conversation_id, 50, 0);
            const isDuplicate = existingMessages.messages.some((msg) => {
              if (task.type === 'image') {
                return msg.image_url === mediaUrl;
              }
              return msg.video_url === mediaUrl;
            });

            if (isDuplicate) {
              logger.info('task:restore', '任务的消息已存在，跳过创建', { taskId: task.external_task_id });
              // 移除占位符，使用已存在的消息
              const existingMessage = existingMessages.messages.find((msg) =>
                task.type === 'image' ? msg.image_url === mediaUrl : msg.video_url === mediaUrl
              );
              if (existingMessage) {
                replaceMediaPlaceholder(task.conversation_id, placeholderId, existingMessage);
              }
              // 先标记未读，再完成任务（从 TaskStore 移至此处，解耦依赖）
              messageCoordinator.markConversationUnread(task.conversation_id);
              completeMediaTask(task.external_task_id);
              return;
            }
          } catch {
            // 查询失败不阻塞，继续创建消息（可能会重复，但不会丢失）
            logger.warn('task:restore', '检查消息是否存在失败', { taskId: task.external_task_id });
          }

          const savedMessage = await createMessage(task.conversation_id, {
            content: successContent,
            role: 'assistant',
            image_url: task.type === 'image' ? mediaUrl : undefined,
            video_url: task.type === 'video' ? mediaUrl : undefined,
            credits_cost: task.credits_locked,
            created_at: placeholderTimestamp, // 使用原始占位符时间戳，确保消息排序正确
            generation_params: undefined, // 任务恢复时不需要存储生成参数
          });

          replaceMediaPlaceholder(task.conversation_id, placeholderId, savedMessage);
          // 使用统一方法添加到缓存（直接存储 API Message 格式，无需转换）
          appendMessage(task.conversation_id, savedMessage);

          // 先标记未读，再完成任务（从 TaskStore 移至此处，解耦依赖）
          messageCoordinator.markConversationUnread(task.conversation_id);
          completeMediaTask(task.external_task_id);
          toast.success(`${task.type === 'image' ? '图片' : '视频'}生成完成`);
        } catch (error) {
          logger.error('task:save', '保存任务结果失败', error, { taskId: task.external_task_id });
          failMediaTask(task.external_task_id);
        }
      },
      onError: async (error: Error) => {
        logger.error('task:restore', '任务恢复失败', error, { taskId: task.external_task_id });
        failMediaTask(task.external_task_id);
        await markTaskAsFailed(task.external_task_id, error.message);
      },
    },
    { interval: pollInterval, maxDuration: remainingTime }
  );
}

async function markTaskAsFailed(externalTaskId: string, reason: string) {
  try {
    await api.post(`/tasks/${externalTaskId}/fail`, { reason });
  } catch (error) {
    logger.error('task:fail', '标记任务失败', error, { taskId: externalTaskId, reason });
  }
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

export async function restoreAllPendingTasks() {
  console.log('[TaskRestore] restoreAllPendingTasks called');
  // 取消之前的待处理恢复
  cancelPendingRestorations();

  const tasks = await fetchPendingTasks();
  console.log('[TaskRestore] fetchPendingTasks returned:', tasks.length, 'tasks', tasks);

  if (tasks.length === 0) {
    console.log('[TaskRestore] no tasks to restore, returning early');
    return;
  }

  console.log('[TaskRestore] preparing to restore tasks...');
  const conversationTitles = new Map<string, string>();
  const { conversations } = useChatStore.getState();
  console.log('[TaskRestore] got conversations:', conversations.length);

  for (const conv of conversations) {
    conversationTitles.set(conv.id, conv.title);
  }

  // 【并发恢复】多个任务同时恢复，不互相阻塞
  // 显示顺序由 created_at 时间戳保证，与恢复顺序无关
  console.log('[TaskRestore] starting task restoration, total:', tasks.length);
  const restorePromises = tasks.map((task, index) => {
    const delay = index * TASK_RESTORE_STAGGER_DELAY;
    console.log('[TaskRestore] scheduling task:', { taskId: task.id, type: task.type, index, delay });
    return new Promise<void>((resolve) => {
      const timeoutId = setTimeout(async () => {
        console.log('[TaskRestore] setTimeout fired for task:', { taskId: task.id, type: task.type });
        try {
          if (task.type === 'chat') {
            // chat 任务：恢复 SSE 流式连接
            console.log('[TaskRestore] calling restoreChatTask for:', task.id);
            await restoreChatTask(task);
          } else {
            // 图片/视频任务：轮询模式
            const title = conversationTitles.get(task.conversation_id) || '进行中的任务';
            restoreTaskPolling(task, title);
          }
        } catch (error) {
          console.log('[TaskRestore] error during restore:', error);
          logger.error('task:restore', '恢复任务失败', error, { taskId: task.id });
        }
        resolve();
      }, delay);
      pendingRestoreTimeouts.push(timeoutId);
    });
  });

  // 等待所有恢复任务启动完成
  await Promise.all(restorePromises);

  const chatTaskCount = tasks.filter(t => t.type === 'chat').length;
  const mediaTaskCount = tasks.length - chatTaskCount;

  if (chatTaskCount > 0 && mediaTaskCount > 0) {
    toast.success(`正在恢复 ${chatTaskCount} 个聊天和 ${mediaTaskCount} 个媒体任务`);
  } else if (chatTaskCount > 0) {
    toast.success(`正在恢复 ${chatTaskCount} 个聊天任务`);
  } else {
    toast.success(`正在恢复 ${mediaTaskCount} 个任务`);
  }
}
