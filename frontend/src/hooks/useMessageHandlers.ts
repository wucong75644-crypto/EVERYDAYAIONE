/**
 * 消息处理 Hook
 *
 * 提取聊天、图像生成、视频生成的处理逻辑
 * 图片/视频任务支持"提交即返回"，轮询在后台通过 useTaskStore 管理
 */

import { type UnifiedModel } from '../constants/models';
import { sendMessageStream, createMessage, type Message, type GenerationParams } from '../services/message';
import {
  createErrorMessage,
  createOptimisticUserMessage,
  createStreamingPlaceholder,
  createMediaTimestamps,
  createMediaOptimisticPair,
} from '../utils/messageFactory';
import {
  generateImage,
  editImage,
  queryTaskStatus as getImageTaskStatus,
  type ImageModel,
  type AspectRatio,
  type ImageResolution,
  type ImageOutputFormat,
} from '../services/image';
import {
  generateTextToVideo,
  generateImageToVideo,
  queryVideoTaskStatus as getVideoTaskStatus,
  type VideoModel,
  type VideoFrames,
  type VideoAspectRatio,
} from '../services/video';
import { useTaskStore } from '../stores/useTaskStore';
import { useChatStore } from '../stores/useChatStore';
import { useConversationRuntimeStore } from '../stores/useConversationRuntimeStore';
import { useAuthStore } from '../stores/useAuthStore';
import axios from 'axios';

/** 媒体生成配置 */
interface MediaGenConfig {
  type: 'image' | 'video';
  conversationId: string;
  conversationTitle: string;
  /** 占位符显示文本（兼容性保留，预创建时可选） */
  placeholderText?: string;
  successContent: string;
  errorPrefix: string;
  pollInterval: number;
  creditsConsumed: number;
  /** 用户消息的时间戳（用于保持消息顺序） */
  userMessageTimestamp: string;
  /** 预创建的占位符ID（在 API 请求前已创建） */
  preCreatedPlaceholderId?: string;
  /** 占位符的时间戳 */
  placeholderTimestamp: string;
  /** 生成参数（用于重新生成时继承） */
  generationParams?: GenerationParams;
  pollFn: (taskId: string) => Promise<{ status: string; fail_msg?: string | null; image_urls?: string[]; video_url?: string | null }>;
  extractMediaUrl: (result: unknown) => { image_url?: string; video_url?: string };
}

/** 媒体生成响应 */
interface MediaResponse {
  status: string;
  task_id: string;
  credits_consumed: number;
  image_urls?: string[];
  video_url?: string | null;
}

interface UseMessageHandlersParams {
  selectedModel: UnifiedModel;
  aspectRatio: AspectRatio;
  resolution: ImageResolution;
  outputFormat: ImageOutputFormat;
  videoFrames: VideoFrames;
  videoAspectRatio: VideoAspectRatio;
  removeWatermark: boolean;
  thinkingEffort?: 'minimal' | 'low' | 'medium' | 'high';
  deepThinkMode?: boolean;
  conversationTitle: string;
  onMessagePending: (message: Message) => void;
  onMessageSent: (aiMessage?: Message | null) => void;
  onStreamContent?: (text: string, conversationId: string) => void;
  onStreamStart?: (conversationId: string, model: string) => void;
  onMediaTaskSubmitted?: () => void;
}

/** 从错误中提取友好消息 */
function extractErrorMessage(error: unknown): string {
  if (axios.isAxiosError(error)) {
    const responseData = error.response?.data;
    const backendMessage =
      responseData?.error?.message || responseData?.message || responseData?.detail;
    return backendMessage || error.message;
  }
  return error instanceof Error ? error.message : '未知错误';
}

/** 安全提取图片URL（带运行时校验） */
function extractImageUrl(result: unknown): string | undefined {
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
function extractVideoUrl(result: unknown): string | undefined {
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

export function useMessageHandlers({
  selectedModel,
  aspectRatio,
  resolution,
  outputFormat,
  videoFrames,
  videoAspectRatio,
  removeWatermark,
  thinkingEffort,
  deepThinkMode,
  conversationTitle,
  onMessagePending,
  onMessageSent,
  onStreamContent,
  onStreamStart,
  onMediaTaskSubmitted,
}: UseMessageHandlersParams) {
  /** 处理生成错误 */
  const handleGenerationError = async (
    conversationId: string,
    errorPrefix: string,
    error: unknown,
    createdAt?: string,
    generationParams?: GenerationParams
  ): Promise<Message> => {
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
  };

  /** 通用媒体生成轮询处理 */
  const handleMediaPolling = (
    response: MediaResponse,
    config: MediaGenConfig
  ) => {
    const { startMediaTask, startPolling, completeMediaTask, failMediaTask } = useTaskStore.getState();
    const { replaceMediaPlaceholder } = useConversationRuntimeStore.getState();
    const { addMessageToCache } = useChatStore.getState();
    const { refreshUser } = useAuthStore.getState();

    const taskId = response.task_id;
    // 使用预创建的占位符ID，或回退到基于taskId的ID（向后兼容）
    const placeholderId = config.preCreatedPlaceholderId || `streaming-${taskId}`;
    // 使用传入的时间戳
    const placeholderTimestamp = config.placeholderTimestamp;

    // 如果没有预创建占位符，才创建新的（向后兼容）
    if (!config.preCreatedPlaceholderId) {
      const placeholderMessage = createStreamingPlaceholder(
        config.conversationId,
        placeholderId,
        config.placeholderText || `${config.type === 'image' ? '图片' : '视频'}生成中...`,
        placeholderTimestamp
      );
      onMessagePending(placeholderMessage);
    }

    // 注册任务（使用真实 taskId 和占位符ID）
    startMediaTask({
      taskId,
      conversationId: config.conversationId,
      conversationTitle: config.conversationTitle,
      type: config.type,
      placeholderId,
    });

    if (onMediaTaskSubmitted) onMediaTaskSubmitted();

    // 开始后台轮询
    startPolling(
      taskId,
      async () => {
        const result = await config.pollFn(taskId);
        if (result.status === 'success') return { done: true, result };
        if (result.status === 'failed') {
          return { done: true, error: new Error(result.fail_msg || `${config.type}生成失败`) };
        }
        return { done: false };
      },
      {
        onSuccess: async (result: unknown) => {
          const mediaUrl = config.extractMediaUrl(result);
          try {
            const savedAiMessage = await createMessage(config.conversationId, {
              content: config.successContent,
              role: 'assistant',
              image_url: mediaUrl.image_url,
              video_url: mediaUrl.video_url,
              credits_cost: config.creditsConsumed,
              created_at: placeholderTimestamp,
              generation_params: config.generationParams,
            });

            // 替换 runtimeStore 中的占位符为真实消息
            // 关键：使用占位符时间戳保持消息顺序（而非后端返回时间）
            const messageWithCorrectTime: Message = {
              ...savedAiMessage,
              created_at: placeholderTimestamp,
            };
            replaceMediaPlaceholder(config.conversationId, placeholderId, messageWithCorrectTime);

            // 同时添加到缓存，确保切换对话后消息仍然显示
            // 注意：需要转换为 useChatStore 的 Message 格式（camelCase）
            // 关键：使用占位符时间戳，避免切换对话后顺序错乱
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
            onMessageSent(savedAiMessage);
          } catch (err) {
            console.error(`保存${config.type}消息失败:`, err);
            failMediaTask(taskId);
          }
        },
        onError: async (error: Error) => {
          console.error(`${config.type}生成失败:`, error);
          const errorMessage = await handleGenerationError(
            config.conversationId,
            config.errorPrefix,
            error,
            placeholderTimestamp,
            config.generationParams
          );
          // 替换 runtimeStore 中的占位符为错误消息
          replaceMediaPlaceholder(config.conversationId, placeholderId, errorMessage);
          failMediaTask(taskId);
          onMessageSent(errorMessage);
        },
      },
      {
        interval: config.pollInterval,
        // 图片最大轮询 10 分钟，视频最大轮询 30 分钟
        maxDuration: config.type === 'image' ? 10 * 60 * 1000 : 30 * 60 * 1000,
      }
    );
  };

  /** 处理聊天消息 */
  const handleChatMessage = async (
    messageContent: string,
    currentConversationId: string,
    imageUrl: string | null = null
  ) => {
    const optimisticUserMessage = createOptimisticUserMessage(
      messageContent,
      currentConversationId,
      imageUrl
    );
    onMessagePending(optimisticUserMessage);

    // 立即创建流式占位符，减少感知延迟
    if (onStreamStart) onStreamStart(currentConversationId, selectedModel.id);

    try {
      await sendMessageStream(
        currentConversationId,
        {
          content: messageContent,
          model_id: selectedModel.id,
          image_url: imageUrl,
          thinking_effort: thinkingEffort,
          thinking_mode: deepThinkMode ? 'deep_think' : 'default',
        },
        {
          onUserMessage: (userMessage: Message) => onMessagePending(userMessage),
          onStart: () => {},
          onContent: (text: string) => {
            if (onStreamContent) onStreamContent(text, currentConversationId);
          },
          onDone: (assistantMessage: Message | null) => onMessageSent(assistantMessage ?? null),
          onError: (error: string) => {
            onMessageSent(createErrorMessage(currentConversationId, 'AI 响应错误', error));
          },
        }
      );
    } catch (error) {
      onMessageSent(createErrorMessage(currentConversationId, '发送失败', error));
    }
  };

  /** 处理图像生成 */
  const handleImageGeneration = async (
    messageContent: string,
    currentConversationId: string,
    imageUrl: string | null = null
  ) => {
    const { replaceMediaPlaceholder } = useConversationRuntimeStore.getState();

    // 预生成时间戳和占位符ID
    const timestamps = createMediaTimestamps();
    const { tempPlaceholderId, userTimestamp: userMessageTimestamp, placeholderTimestamp } = timestamps;

    // 立即显示乐观消息对（用户消息 + 占位符）
    const { userMessage, placeholder } = createMediaOptimisticPair(
      currentConversationId,
      messageContent,
      imageUrl,
      '图片生成中...',
      timestamps
    );
    onMessagePending(userMessage);
    onMessagePending(placeholder);

    // 构建图片生成参数（用于重新生成时继承，放在 try 外以便 catch 也能访问）
    const imageGenerationParams: GenerationParams = {
      image: {
        aspectRatio,
        resolution,
        outputFormat,
        model: selectedModel.id,
      },
    };

    try {
      // 并行：保存用户消息 + 请求图片生成
      const [, response] = await Promise.all([
        createMessage(currentConversationId, {
          content: messageContent,
          role: 'user',
          image_url: imageUrl,
          created_at: userMessageTimestamp,
        }).then((realUserMessage) => onMessagePending(realUserMessage)),
        imageUrl
          ? editImage({
              prompt: messageContent,
              image_urls: [imageUrl],
              size: aspectRatio,
              output_format: outputFormat,
              wait_for_result: false,
            })
          : generateImage({
              prompt: messageContent,
              model: selectedModel.id as ImageModel,
              size: aspectRatio,
              output_format: outputFormat,
              resolution: selectedModel.supportsResolution ? resolution : undefined,
              wait_for_result: false,
            }),
      ]);

      const successContent = imageUrl ? '图片编辑完成' : '图片已生成完成';

      if (response.status === 'pending' || response.status === 'processing') {
        handleMediaPolling(response, {
          type: 'image',
          conversationId: currentConversationId,
          conversationTitle,
          successContent,
          errorPrefix: '图片处理失败',
          pollInterval: 2000,
          creditsConsumed: response.credits_consumed,
          userMessageTimestamp,
          placeholderTimestamp,
          preCreatedPlaceholderId: tempPlaceholderId,
          generationParams: imageGenerationParams,
          pollFn: getImageTaskStatus,
          extractMediaUrl: (r) => ({ image_url: extractImageUrl(r) }),
        });
      } else if (response.status === 'success' && response.image_urls?.length) {
        const savedAiMessage = await createMessage(currentConversationId, {
          content: successContent,
          role: 'assistant',
          image_url: response.image_urls[0],
          credits_cost: response.credits_consumed,
          created_at: placeholderTimestamp,
          generation_params: imageGenerationParams,
        });
        replaceMediaPlaceholder(currentConversationId, tempPlaceholderId, savedAiMessage);
        onMessageSent(savedAiMessage);
        if (onMediaTaskSubmitted) onMediaTaskSubmitted();
      } else {
        throw new Error('图片处理失败');
      }
    } catch (error) {
      const errorMessage = await handleGenerationError(
        currentConversationId,
        '图片处理失败',
        error,
        placeholderTimestamp,
        imageGenerationParams
      );
      replaceMediaPlaceholder(currentConversationId, tempPlaceholderId, errorMessage);
      onMessageSent(errorMessage);
      if (onMediaTaskSubmitted) onMediaTaskSubmitted();
    }
  };

  /** 处理视频生成 */
  const handleVideoGeneration = async (
    messageContent: string,
    currentConversationId: string,
    imageUrl: string | null = null
  ) => {
    const { replaceMediaPlaceholder } = useConversationRuntimeStore.getState();
    const isImageToVideo = imageUrl && selectedModel.capabilities.imageToVideo;

    // 预生成时间戳和占位符ID
    const timestamps = createMediaTimestamps();
    const { tempPlaceholderId, userTimestamp: userMessageTimestamp, placeholderTimestamp } = timestamps;

    // 立即显示乐观消息对（用户消息 + 占位符）
    const { userMessage, placeholder } = createMediaOptimisticPair(
      currentConversationId,
      messageContent,
      isImageToVideo ? imageUrl : null,
      '视频生成中...',
      timestamps
    );
    onMessagePending(userMessage);
    onMessagePending(placeholder);

    // 构建视频生成参数（用于重新生成时继承，放在 try 外以便 catch 也能访问）
    const videoGenerationParams: GenerationParams = {
      video: {
        frames: videoFrames,
        aspectRatio: videoAspectRatio,
        removeWatermark,
        model: selectedModel.id,
      },
    };

    try {
      // 并行：保存用户消息 + 请求视频生成
      const [, response] = await Promise.all([
        createMessage(currentConversationId, {
          content: messageContent,
          role: 'user',
          image_url: isImageToVideo ? imageUrl : null,
          created_at: userMessageTimestamp,
        }).then((realUserMessage) => onMessagePending(realUserMessage)),
        isImageToVideo
          ? generateImageToVideo({
              prompt: messageContent,
              image_url: imageUrl,
              model: selectedModel.id as VideoModel,
              n_frames: videoFrames,
              aspect_ratio: videoAspectRatio,
              remove_watermark: removeWatermark,
              wait_for_result: false,
            })
          : generateTextToVideo({
              prompt: messageContent,
              model: selectedModel.id as VideoModel,
              n_frames: videoFrames,
              aspect_ratio: videoAspectRatio,
              remove_watermark: removeWatermark,
              wait_for_result: false,
            }),
      ]);

      const successContent = isImageToVideo ? '视频生成完成（图生视频）' : '视频生成完成';

      if (response.status === 'pending' || response.status === 'processing') {
        handleMediaPolling(response, {
          type: 'video',
          conversationId: currentConversationId,
          conversationTitle,
          successContent,
          errorPrefix: '视频生成失败',
          pollInterval: 5000,
          creditsConsumed: response.credits_consumed,
          userMessageTimestamp,
          placeholderTimestamp,
          preCreatedPlaceholderId: tempPlaceholderId,
          generationParams: videoGenerationParams,
          pollFn: getVideoTaskStatus,
          extractMediaUrl: (r) => ({ video_url: extractVideoUrl(r) }),
        });
      } else if (response.status === 'success' && response.video_url) {
        const savedAiMessage = await createMessage(currentConversationId, {
          content: successContent,
          role: 'assistant',
          video_url: response.video_url,
          credits_cost: response.credits_consumed,
          created_at: placeholderTimestamp,
          generation_params: videoGenerationParams,
        });
        replaceMediaPlaceholder(currentConversationId, tempPlaceholderId, savedAiMessage);
        onMessageSent(savedAiMessage);
        if (onMediaTaskSubmitted) onMediaTaskSubmitted();
      } else {
        throw new Error('视频生成失败');
      }
    } catch (error) {
      const errorMessage = await handleGenerationError(
        currentConversationId,
        '视频生成失败',
        error,
        placeholderTimestamp,
        videoGenerationParams
      );
      replaceMediaPlaceholder(currentConversationId, tempPlaceholderId, errorMessage);
      onMessageSent(errorMessage);
      if (onMediaTaskSubmitted) onMediaTaskSubmitted();
    }
  };

  return {
    handleChatMessage,
    handleImageGeneration,
    handleVideoGeneration,
  };
}
