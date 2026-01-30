/**
 * 任务恢复工具
 */

import { useTaskStore } from '../stores/useTaskStore';
import { useChatStore } from '../stores/useChatStore';
import { useConversationRuntimeStore } from '../stores/useConversationRuntimeStore';
import { queryTaskStatus as getImageTaskStatus, type TaskStatusResponse as ImageTaskStatusResponse } from '../services/image';
import { queryVideoTaskStatus as getVideoTaskStatus, type TaskStatusResponse as VideoTaskStatusResponse } from '../services/video';
import { createMessage, getMessages } from '../services/message';
import { createStreamingPlaceholder } from './messageFactory';
import api from '../services/api';
import toast from 'react-hot-toast';
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
  [key: string]: string | undefined;
}

interface PendingTask {
  id: string;
  external_task_id: string;
  conversation_id: string;
  type: 'image' | 'video';
  status: string;
  request_params: TaskRequestParams;
  credits_locked: number;
  placeholder_message_id: string | null;
  started_at: string;
  last_polled_at: string | null;
}

type TaskResult = ImageTaskStatusResponse | VideoTaskStatusResponse;

export async function fetchPendingTasks(): Promise<PendingTask[]> {
  try {
    const response = await api.get<{ tasks: PendingTask[]; count: number }>('/tasks/pending');
    return response.data.tasks || [];
  } catch (error) {
    console.error('获取进行中任务失败:', error);
    return [];
  }
}

export function restoreTaskPolling(task: PendingTask, conversationTitle: string) {
  const { startMediaTask, startPolling, completeMediaTask, failMediaTask } =
    useTaskStore.getState();
  const { replaceMediaPlaceholder, addMediaPlaceholder } = useConversationRuntimeStore.getState();
  const { addMessageToCache } = useChatStore.getState();

  const maxDuration = task.type === 'image' ? IMAGE_TASK_TIMEOUT : VIDEO_TASK_TIMEOUT;
  const elapsed = Date.now() - new Date(task.started_at).getTime();

  if (elapsed > maxDuration) {
    console.warn(`任务 ${task.external_task_id} 已超时,跳过恢复`);
    markTaskAsFailed(task.external_task_id, '任务超时');
    return;
  }

  // 验证对话 ID 有效性（任务可能是在临时对话中创建的，此时 conversation_id 为 null）
  if (!task.conversation_id) {
    console.warn(`任务 ${task.external_task_id} 没有关联对话,跳过恢复`);
    markTaskAsFailed(task.external_task_id, '任务未关联对话');
    return;
  }

  const placeholderId = task.placeholder_message_id ||
    `restored-${task.external_task_id}`;

  startMediaTask({
    taskId: task.external_task_id,
    conversationId: task.conversation_id,
    conversationTitle,
    type: task.type,
    placeholderId,
  });

  // 创建占位符消息并添加到 RuntimeStore，确保刷新后 UI 能显示"生成中"状态
  // 使用当前时间戳而非任务开始时间，确保占位符显示在消息列表末尾
  // 避免插入到历史消息中间导致列表跳动
  const loadingText = task.type === 'image' ? '图片生成中...' : '视频生成中...';
  const placeholder = createStreamingPlaceholder(
    task.conversation_id,
    placeholderId,
    loadingText,
    new Date().toISOString()
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
              console.info(`任务 ${task.external_task_id} 的消息已存在，跳过创建`);
              // 移除占位符，使用已存在的消息
              const existingMessage = existingMessages.messages.find((msg) =>
                task.type === 'image' ? msg.image_url === mediaUrl : msg.video_url === mediaUrl
              );
              if (existingMessage) {
                replaceMediaPlaceholder(task.conversation_id, placeholderId, existingMessage);
              }
              completeMediaTask(task.external_task_id);
              return;
            }
          } catch (checkError) {
            // 查询失败不阻塞，继续创建消息（可能会重复，但不会丢失）
            console.warn('检查消息是否存在失败:', checkError);
          }

          const savedMessage = await createMessage(task.conversation_id, {
            content: successContent,
            role: 'assistant',
            image_url: task.type === 'image' ? mediaUrl : undefined,
            video_url: task.type === 'video' ? mediaUrl : undefined,
            credits_cost: task.credits_locked,
            generation_params: undefined, // 任务恢复时不需要存储生成参数
          });

          replaceMediaPlaceholder(task.conversation_id, placeholderId, savedMessage);
          addMessageToCache(task.conversation_id, {
            id: savedMessage.id,
            role: 'assistant',
            content: savedMessage.content,
            imageUrl: savedMessage.image_url || undefined,
            videoUrl: savedMessage.video_url || undefined,
            createdAt: savedMessage.created_at,
          });

          completeMediaTask(task.external_task_id);
          toast.success(`${task.type === 'image' ? '图片' : '视频'}生成完成`);
        } catch (error) {
          console.error('保存任务结果失败:', error);
          failMediaTask(task.external_task_id);
        }
      },
      onError: async (error: Error) => {
        console.error('任务恢复失败:', error);
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
    console.error('标记任务失败:', error);
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
  // 取消之前的待处理恢复
  cancelPendingRestorations();

  const tasks = await fetchPendingTasks();

  if (tasks.length === 0) {
    return;
  }

  const conversationTitles = new Map<string, string>();
  const { conversations } = useChatStore.getState();

  for (const conv of conversations) {
    conversationTitles.set(conv.id, conv.title);
  }

  for (const [index, task] of tasks.entries()) {
    const timeoutId = setTimeout(() => {
      const title = conversationTitles.get(task.conversation_id) || '未知对话';
      restoreTaskPolling(task, title);
    }, index * TASK_RESTORE_STAGGER_DELAY);
    pendingRestoreTimeouts.push(timeoutId);
  }

  toast.success(`正在恢复 ${tasks.length} 个任务`);
}
