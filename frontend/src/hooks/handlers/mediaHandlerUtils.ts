/**
 * 消息处理器共享工具
 * 包含类型定义、错误处理、URL提取等工具函数
 */

import axios from 'axios';
import { createMessage, type Message, type GenerationParams } from '../../services/message';
import { createErrorMessage, createStreamingPlaceholder } from '../../utils/messageFactory';
import { useTaskStore } from '../../stores/useTaskStore';
import { useConversationRuntimeStore } from '../../stores/useConversationRuntimeStore';
import { useChatStore } from '../../stores/useChatStore';
import { useAuthStore } from '../../stores/useAuthStore';

/** 媒体生成配置 */
export interface MediaGenConfig {
  type: 'image' | 'video';
  conversationId: string;
  conversationTitle: string;
  placeholderText?: string;
  successContent: string;
  errorPrefix: string;
  pollInterval: number;
  maxDuration: number;
  creditsConsumed: number;
  userMessageTimestamp: string;
  preCreatedPlaceholderId?: string;
  placeholderTimestamp: string;
  generationParams?: GenerationParams;
  pollFn: (taskId: string) => Promise<{
    status: string;
    fail_msg?: string | null;
    image_urls?: string[];
    video_url?: string | null
  }>;
  extractMediaUrl: (result: unknown) => { image_url?: string; video_url?: string };
  shouldPreloadImage?: boolean;
}

/** 媒体生成响应 */
export interface MediaResponse {
  status: string;
  task_id: string;
  credits_consumed: number;
  image_urls?: string[];
  video_url?: string | null;
}

/** 从错误中提取友好消息 */
export function extractErrorMessage(error: unknown): string {
  if (axios.isAxiosError(error)) {
    const responseData = error.response?.data;
    const backendMessage =
      responseData?.error?.message || responseData?.message || responseData?.detail;
    return backendMessage || error.message;
  }
  return error instanceof Error ? error.message : '未知错误';
}

/** 安全提取图片URL（带运行时校验） */
export function extractImageUrl(result: unknown): string | undefined {
  if (
    result &&
    typeof result === 'object' &&
    'image_urls' in result &&
    Array.isArray((result as { image_urls: unknown }).image_urls) &&
    typeof (result as { image_urls: string[] }).image_urls[0] === 'string'
  ) {
    return (result as { image_urls: string[] }).image_urls[0];
  }
  return undefined;
}

/** 安全提取视频URL（带运行时校验） */
export function extractVideoUrl(result: unknown): string | undefined {
  if (
    result &&
    typeof result === 'object' &&
    'video_url' in result &&
    typeof (result as { video_url: unknown }).video_url === 'string'
  ) {
    return (result as { video_url: string }).video_url;
  }
  return undefined;
}

/** 处理生成错误 */
export async function handleGenerationError(
  conversationId: string,
  errorPrefix: string,
  error: unknown,
  createdAt?: string,
  generationParams?: GenerationParams
): Promise<Message> {
  const errorMsg = extractErrorMessage(error);
  try {
    return await createMessage(conversationId, {
      content: `${errorPrefix}: ${errorMsg}`,
      role: 'assistant',
      is_error: true,
      created_at: createdAt,
      generation_params: generationParams,
    });
  } catch {
    return createErrorMessage(conversationId, errorPrefix, error, createdAt);
  }
}

/**
 * 通用媒体生成轮询处理
 * 提取的共享逻辑，用于图片和视频生成
 */
export function createMediaPollingHandler(
  response: MediaResponse,
  config: MediaGenConfig,
  callbacks: {
    onMessagePending: (message: Message) => void;
    onMessageSent: (message?: Message | null) => void;
    onMediaTaskSubmitted?: () => void;
  }
) {
  const { startMediaTask, startPolling, completeMediaTask, failMediaTask } =
    useTaskStore.getState();
  const { replaceMediaPlaceholder } = useConversationRuntimeStore.getState();
  const { addMessageToCache } = useChatStore.getState();
  const { refreshUser } = useAuthStore.getState();

  const taskId = response.task_id;
  const placeholderId = config.preCreatedPlaceholderId || `streaming-${taskId}`;
  const placeholderTimestamp = config.placeholderTimestamp;

  // 创建占位符消息（如果未预创建）
  if (!config.preCreatedPlaceholderId) {
    const placeholderMessage = createStreamingPlaceholder(
      config.conversationId,
      placeholderId,
      config.placeholderText || `${config.type === 'image' ? '图片' : '视频'}生成中...`,
      placeholderTimestamp
    );
    callbacks.onMessagePending(placeholderMessage);
  }

  // 启动媒体任务
  startMediaTask({
    taskId,
    conversationId: config.conversationId,
    conversationTitle: config.conversationTitle,
    type: config.type,
    placeholderId,
  });

  if (callbacks.onMediaTaskSubmitted) callbacks.onMediaTaskSubmitted();

  // 开始轮询
  startPolling(
    taskId,
    async () => {
      const result = await config.pollFn(taskId);
      if (result.status === 'success') return { done: true, result };
      if (result.status === 'failed') {
        return {
          done: true,
          error: new Error(result.fail_msg || `${config.type === 'image' ? '图片' : '视频'}生成失败`)
        };
      }
      return { done: false };
    },
    {
      onSuccess: async (result: unknown) => {
        const mediaUrl = config.extractMediaUrl(result);
        try {
          // 预加载图片（仅图片类型）
          if (config.shouldPreloadImage && mediaUrl.image_url) {
            const img = new Image();
            img.src = mediaUrl.image_url;
          }

          // 保存消息
          const savedAiMessage = await createMessage(config.conversationId, {
            content: config.successContent,
            role: 'assistant',
            image_url: mediaUrl.image_url,
            video_url: mediaUrl.video_url,
            credits_cost: config.creditsConsumed,
            created_at: placeholderTimestamp,
            generation_params: config.generationParams,
          });

          const messageWithCorrectTime: Message = {
            ...savedAiMessage,
            created_at: placeholderTimestamp,
          };

          replaceMediaPlaceholder(config.conversationId, placeholderId, messageWithCorrectTime);

          addMessageToCache(config.conversationId, {
            id: savedAiMessage.id,
            role: savedAiMessage.role as 'user' | 'assistant',
            content: savedAiMessage.content,
            imageUrl: savedAiMessage.image_url ?? undefined,
            videoUrl: savedAiMessage.video_url ?? undefined,
            createdAt: placeholderTimestamp,
          });

          completeMediaTask(taskId);
          refreshUser();
          callbacks.onMessageSent(savedAiMessage);
        } catch (err) {
          console.error(`保存${config.type === 'image' ? '图片' : '视频'}消息失败:`, err);
          failMediaTask(taskId);
        }
      },
      onError: async (error: Error) => {
        console.error(`${config.type === 'image' ? '图片' : '视频'}生成失败:`, error);
        const errorMessage = await handleGenerationError(
          config.conversationId,
          config.errorPrefix,
          error,
          placeholderTimestamp,
          config.generationParams
        );
        replaceMediaPlaceholder(config.conversationId, placeholderId, errorMessage);
        failMediaTask(taskId);
        callbacks.onMessageSent(errorMessage);
      },
    },
    {
      interval: config.pollInterval,
      maxDuration: config.maxDuration,
    }
  );
}
