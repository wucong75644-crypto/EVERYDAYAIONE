/**
 * 媒体生成核心逻辑
 *
 * 底层函数，只负责：API 调用 + 轮询处理 + 消息保存
 * 不负责：用户消息创建、乐观更新
 *
 * 被 imageSender、videoSender、imageStrategy、videoStrategy 复用
 */

import { createMessage, type Message, type GenerationParams } from '../message';
import { generateImage, editImage, queryTaskStatus as getImageTaskStatus, type ImageModel } from '../image';
import {
  generateTextToVideo,
  generateImageToVideo,
  queryVideoTaskStatus as getVideoTaskStatus,
  type VideoModel,
} from '../video';
import { useTaskStore } from '../../stores/useTaskStore';
import { useAuthStore } from '../../stores/useAuthStore';
import { extractErrorMessage } from '../../hooks/handlers/mediaHandlerUtils';
import { ALL_MODELS } from '../../constants/models';
import { parseImageUrls } from '../../utils/imageUtils';
import toast from 'react-hot-toast';

/** 媒体生成结果回调 */
export interface MediaGenerationCallbacks {
  /** 生成成功 */
  onSuccess: (savedMessage: Message) => void;
  /** 生成失败 */
  onError: (errorMessage: Message) => void;
  /** 任务已提交到后台轮询 */
  onTaskSubmitted?: () => void;
}

/** 图片生成核心参数 */
export interface ImageGenerationCoreParams {
  conversationId: string;
  prompt: string;
  imageUrl?: string | null;
  modelId: string;
  generationParams: GenerationParams & { image: NonNullable<GenerationParams['image']> };
  conversationTitle?: string;
  /** 消息创建时间戳 */
  messageTimestamp: string;
  /** 任务占位符 ID（用于任务管理） */
  placeholderId: string;
  callbacks: MediaGenerationCallbacks;
}

/** 视频生成核心参数 */
export interface VideoGenerationCoreParams {
  conversationId: string;
  prompt: string;
  imageUrl?: string | null;
  modelId: string;
  generationParams: GenerationParams & { video: NonNullable<GenerationParams['video']> };
  conversationTitle?: string;
  messageTimestamp: string;
  placeholderId: string;
  callbacks: MediaGenerationCallbacks;
}

/**
 * 图片生成核心逻辑
 * 只负责 API 调用和轮询，不创建用户消息
 */
export async function executeImageGenerationCore(params: ImageGenerationCoreParams): Promise<void> {
  const {
    conversationId,
    prompt,
    imageUrl,
    modelId,
    generationParams,
    conversationTitle = '',
    messageTimestamp,
    placeholderId,
    callbacks,
  } = params;

  const { onSuccess, onError, onTaskSubmitted } = callbacks;
  const imageParams = generationParams.image;
  const { aspectRatio, outputFormat, resolution } = imageParams;

  // 判断模型是否支持 resolution
  const modelConfig = ALL_MODELS.find((m) => m.id === modelId);
  const supportsResolution = modelConfig?.supportsResolution ?? false;
  const finalResolution = supportsResolution ? resolution : undefined;

  const successContent = imageUrl ? '图片编辑完成' : '图片已生成完成';

  try {
    const response = imageUrl
      ? await editImage({
          prompt,
          image_urls: parseImageUrls(imageUrl),
          size: aspectRatio,
          output_format: outputFormat,
          wait_for_result: false,
          conversation_id: conversationId,
        })
      : await generateImage({
          prompt,
          model: modelId as ImageModel,
          size: aspectRatio,
          output_format: outputFormat,
          resolution: finalResolution,
          wait_for_result: false,
          conversation_id: conversationId,
        });

    if (response.status === 'pending' || response.status === 'processing') {
      // 启动后台轮询
      startMediaPolling({
        type: 'image',
        taskId: response.task_id,
        conversationId,
        conversationTitle,
        placeholderId,
        creditsConsumed: response.credits_consumed,
        messageTimestamp,
        generationParams,
        successContent,
        errorPrefix: '图片处理失败',
        pollFn: getImageTaskStatus,
        extractMediaUrl: (r) => (r as { image_urls: string[] }).image_urls[0],
        shouldPreloadImage: true,
        callbacks,
      });
      onTaskSubmitted?.();
    } else if (response.status === 'success' && response.image_urls?.length) {
      // 同步完成
      const savedMessage = await createMessage(conversationId, {
        content: successContent,
        role: 'assistant',
        image_url: response.image_urls[0],
        credits_cost: response.credits_consumed,
        created_at: messageTimestamp,
        generation_params: generationParams,
      });
      onSuccess(savedMessage);
    } else {
      throw new Error('图片处理失败');
    }
  } catch (error) {
    const errorMessage = await createErrorMediaMessage(
      conversationId,
      '图片处理失败',
      error,
      messageTimestamp,
      generationParams
    );
    onError(errorMessage);
  }
}

/**
 * 视频生成核心逻辑
 * 只负责 API 调用和轮询，不创建用户消息
 */
export async function executeVideoGenerationCore(params: VideoGenerationCoreParams): Promise<void> {
  const {
    conversationId,
    prompt,
    imageUrl,
    modelId,
    generationParams,
    conversationTitle = '',
    messageTimestamp,
    placeholderId,
    callbacks,
  } = params;

  const { onSuccess, onError, onTaskSubmitted } = callbacks;
  const videoParams = generationParams.video;
  const { frames, aspectRatio, removeWatermark } = videoParams;

  // 判断是否图生视频
  const modelConfig = ALL_MODELS.find((m) => m.id === modelId);
  const supportsI2V = modelConfig?.type === 'video' && modelConfig.capabilities.imageToVideo;
  const isImageToVideo = imageUrl && supportsI2V;

  const successContent = isImageToVideo ? '视频生成完成（图生视频）' : '视频生成完成';

  try {
    const response = isImageToVideo
      ? await generateImageToVideo({
          prompt,
          image_url: imageUrl,
          model: modelId as VideoModel,
          n_frames: frames,
          aspect_ratio: aspectRatio,
          remove_watermark: removeWatermark,
          wait_for_result: false,
          conversation_id: conversationId,
        })
      : await generateTextToVideo({
          prompt,
          model: modelId as VideoModel,
          n_frames: frames,
          aspect_ratio: aspectRatio,
          remove_watermark: removeWatermark,
          wait_for_result: false,
          conversation_id: conversationId,
        });

    if (response.status === 'pending' || response.status === 'processing') {
      // 启动后台轮询
      startMediaPolling({
        type: 'video',
        taskId: response.task_id,
        conversationId,
        conversationTitle,
        placeholderId,
        creditsConsumed: response.credits_consumed,
        messageTimestamp,
        generationParams,
        successContent,
        errorPrefix: '视频生成失败',
        pollFn: getVideoTaskStatus,
        extractMediaUrl: (r) => (r as { video_url: string }).video_url,
        shouldPreloadImage: false,
        callbacks,
      });
      onTaskSubmitted?.();
    } else if (response.status === 'success' && response.video_url) {
      // 同步完成
      const savedMessage = await createMessage(conversationId, {
        content: successContent,
        role: 'assistant',
        video_url: response.video_url,
        credits_cost: response.credits_consumed,
        created_at: messageTimestamp,
        generation_params: generationParams,
      });
      onSuccess(savedMessage);
    } else {
      throw new Error('视频生成失败');
    }
  } catch (error) {
    const errorMessage = await createErrorMediaMessage(
      conversationId,
      '视频生成失败',
      error,
      messageTimestamp,
      generationParams
    );
    onError(errorMessage);
  }
}

/** 轮询配置 */
interface MediaPollingConfig {
  type: 'image' | 'video';
  taskId: string;
  conversationId: string;
  conversationTitle: string;
  placeholderId: string;
  creditsConsumed: number;
  messageTimestamp: string;
  generationParams: GenerationParams;
  successContent: string;
  errorPrefix: string;
  pollFn: (taskId: string) => Promise<{ status: string; fail_msg?: string | null }>;
  extractMediaUrl: (result: unknown) => string;
  shouldPreloadImage: boolean;
  callbacks: MediaGenerationCallbacks;
}

/** 启动媒体轮询 */
function startMediaPolling(config: MediaPollingConfig): void {
  const { startMediaTask, startPolling, completeMediaTask, failMediaTask } = useTaskStore.getState();
  const { refreshUser } = useAuthStore.getState();

  const {
    type,
    taskId,
    conversationId,
    conversationTitle,
    placeholderId,
    creditsConsumed,
    messageTimestamp,
    generationParams,
    successContent,
    errorPrefix,
    pollFn,
    extractMediaUrl,
    shouldPreloadImage,
    callbacks,
  } = config;

  const { onSuccess, onError } = callbacks;
  const pollInterval = type === 'image' ? 2000 : 5000;
  const maxDuration = type === 'image' ? 10 * 60 * 1000 : 30 * 60 * 1000;

  // 注册任务
  if (conversationTitle) {
    startMediaTask({
      taskId,
      conversationId,
      conversationTitle,
      type,
      placeholderId,
    });
  }

  // 开始轮询
  startPolling(
    taskId,
    async () => {
      const result = await pollFn(taskId);
      if (result.status === 'success') return { done: true, result };
      if (result.status === 'failed') {
        return { done: true, error: new Error(result.fail_msg || `${type === 'image' ? '图片' : '视频'}生成失败`) };
      }
      return { done: false };
    },
    {
      onSuccess: async (result: unknown) => {
        const mediaUrl = extractMediaUrl(result);

        // 预加载图片
        if (shouldPreloadImage && mediaUrl) {
          const img = new Image();
          img.src = mediaUrl;
        }

        try {
          const savedMessage = await createMessage(conversationId, {
            content: successContent,
            role: 'assistant',
            image_url: type === 'image' ? mediaUrl : undefined,
            video_url: type === 'video' ? mediaUrl : undefined,
            credits_cost: creditsConsumed,
            created_at: messageTimestamp,
            generation_params: generationParams,
          });
          completeMediaTask(taskId);
          refreshUser();
          onSuccess(savedMessage);
        } catch (err) {
          console.error(`保存${type === 'image' ? '图片' : '视频'}消息失败:`, err);
          failMediaTask(taskId);
        }
      },
      onError: async (error: Error) => {
        const errorMessage = await createErrorMediaMessage(
          conversationId,
          errorPrefix,
          error,
          messageTimestamp,
          generationParams
        );
        failMediaTask(taskId);
        toast.error(`${errorPrefix}: ${extractErrorMessage(error)}`);
        onError(errorMessage);
      },
    },
    { interval: pollInterval, maxDuration }
  );
}

/** 创建错误媒体消息 */
async function createErrorMediaMessage(
  conversationId: string,
  errorPrefix: string,
  error: unknown,
  createdAt: string,
  generationParams: GenerationParams
): Promise<Message> {
  const errorMsg = `${errorPrefix}: ${extractErrorMessage(error)}`;
  try {
    return await createMessage(conversationId, {
      content: errorMsg,
      role: 'assistant',
      is_error: true,
      created_at: createdAt,
      generation_params: generationParams,
    });
  } catch {
    // 保存失败时返回本地消息
    return {
      id: `error-${Date.now()}`,
      conversation_id: conversationId,
      role: 'assistant',
      content: errorMsg,
      is_error: true,
      created_at: createdAt,
      generation_params: generationParams,
      image_url: null,
      video_url: null,
      credits_cost: undefined,
    };
  }
}
