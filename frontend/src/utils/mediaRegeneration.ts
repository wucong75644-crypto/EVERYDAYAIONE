/**
 * 媒体重新生成工具
 *
 * 包含图片/视频重新生成的通用逻辑和工具函数
 */

import { createMessage, type Message, type GenerationParams } from '../services/message';
import { generateImage, editImage, queryTaskStatus as getImageTaskStatus, type ImageModel } from '../services/image';
import { generateTextToVideo, generateImageToVideo, queryVideoTaskStatus as getVideoTaskStatus, type VideoModel } from '../services/video';
import { useTaskStore } from '../stores/useTaskStore';
import { useAuthStore } from '../stores/useAuthStore';
import toast from 'react-hot-toast';
import { createTempMessagePair } from './messageFactory';
import { type UnifiedModel, ALL_MODELS } from '../constants/models';
import { getSavedSettings } from './settingsStorage';
import { extractErrorMessage } from '../hooks/handlers/mediaHandlerUtils';

/** 默认参数 */
export const MEDIA_DEFAULTS = {
  IMAGE_MODEL: 'google/nano-banana',
  VIDEO_MODEL: 'sora-2-text-to-video',
  I2V_MODEL: 'sora-2-image-to-video',
};

/** 根据模型 ID 获取模型类型 */
export function getModelTypeById(modelId: string): 'chat' | 'image' | 'video' | null {
  return ALL_MODELS.find((m) => m.id === modelId)?.type ?? null;
}

/** 媒体重新生成配置 */
export interface MediaRegenConfig {
  type: 'image' | 'video';
  placeholderText: string;
  successContent: string;
  pollInterval: number;
  userMessageTimestamp: string;
  generationParams?: GenerationParams;
  pollFn: (taskId: string) => Promise<{ status: string; fail_msg?: string | null }>;
  extractUrl: (result: unknown) => { image_url?: string; video_url?: string };
}

/** 保存用户消息到数据库 */
export async function saveUserMessage(
  conversationId: string,
  userMessage: Message,
  tempUserId: string,
  setMessages: React.Dispatch<React.SetStateAction<Message[]>>,
  createdAt: string
): Promise<Message> {
  const realUserMessage = await createMessage(conversationId, {
    content: userMessage.content,
    role: 'user',
    image_url: userMessage.image_url,
    created_at: createdAt,
  });
  setMessages((prev) =>
    prev.map((m) => (m.id === tempUserId ? { ...realUserMessage, created_at: m.created_at } : m))
  );
  return realUserMessage;
}

/** 处理重新生成错误 */
export async function handleRegenError(
  error: unknown,
  conversationId: string,
  placeholderId: string,
  mediaType: 'image' | 'video',
  setMessages: React.Dispatch<React.SetStateAction<Message[]>>,
  createdAt: string,
  generationParams?: GenerationParams
): Promise<void> {
  const errorText = mediaType === 'image' ? '图片生成失败' : '视频生成失败';
  const errorMsg = `${errorText}: ${extractErrorMessage(error)}`;

  try {
    const savedError = await createMessage(conversationId, {
      content: errorMsg,
      role: 'assistant',
      is_error: true,
      created_at: createdAt,
      generation_params: generationParams,
    });
    setMessages((prev) => prev.map((m) => (m.id === placeholderId ? savedError : m)));
  } catch {
    setMessages((prev) =>
      prev.map((m) => (m.id === placeholderId ? { ...m, content: errorMsg, is_error: true } : m))
    );
  }
  toast.error(errorMsg);
}

/** 通用媒体轮询处理 */
export function handleMediaPolling(
  taskId: string,
  placeholderId: string,
  creditsConsumed: number,
  config: MediaRegenConfig,
  conversationId: string,
  conversationTitle: string,
  setMessages: React.Dispatch<React.SetStateAction<Message[]>>,
  onMessageUpdate?: (newLastMessage: string) => void,
  resetRegeneratingState?: () => void,
  onMediaTaskSubmitted?: () => void
): void {
  const { startMediaTask, startPolling, completeMediaTask, failMediaTask } = useTaskStore.getState();
  const { refreshUser } = useAuthStore.getState();

  startMediaTask({
    taskId,
    conversationId,
    conversationTitle,
    type: config.type,
    placeholderId,
  });

  resetRegeneratingState?.();
  onMediaTaskSubmitted?.();

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
        const mediaUrl = config.extractUrl(result);
        const aiCreatedAt = new Date(new Date(config.userMessageTimestamp).getTime() + 1).toISOString();
        try {
          const savedMsg = await createMessage(conversationId, {
            content: config.successContent,
            role: 'assistant',
            image_url: mediaUrl.image_url,
            video_url: mediaUrl.video_url,
            credits_cost: creditsConsumed,
            created_at: aiCreatedAt,
            generation_params: config.generationParams,
          });
          setMessages((prev) => prev.map((m) => (m.id === placeholderId ? savedMsg : m)));
          completeMediaTask(taskId);
          refreshUser();
          if (onMessageUpdate) onMessageUpdate(savedMsg.content);
        } catch (err) {
          console.error(`保存${config.type}消息失败:`, err);
          failMediaTask(taskId);
        }
      },
      onError: async (error: Error) => {
        const errorCreatedAt = new Date(new Date(config.userMessageTimestamp).getTime() + 1).toISOString();
        await handleRegenError(error, conversationId, placeholderId, config.type, setMessages, errorCreatedAt, config.generationParams);
        failMediaTask(taskId);
      },
    },
    {
      interval: config.pollInterval,
      maxDuration: config.type === 'image' ? 10 * 60 * 1000 : 30 * 60 * 1000,
    }
  );
}

/** 图片重新生成参数 */
interface ImageRegenParams {
  conversationId: string;
  userMessage: Message;
  originalGenerationParams?: GenerationParams | null;
  modelId?: string | null;
  selectedModel?: UnifiedModel | null;
  setMessages: React.Dispatch<React.SetStateAction<Message[]>>;
  scrollToBottom: (smooth?: boolean) => void;
  setRegeneratingId: (id: string | null) => void;
  setIsRegeneratingAI: (value: boolean) => void;
  conversationTitle: string;
  onMessageUpdate?: (newLastMessage: string) => void;
  resetRegeneratingState: () => void;
  onMediaTaskSubmitted?: () => void;
}

/** 执行图片重新生成 */
export async function executeImageRegeneration({
  conversationId,
  userMessage,
  originalGenerationParams,
  modelId,
  selectedModel,
  setMessages,
  scrollToBottom,
  setRegeneratingId,
  setIsRegeneratingAI,
  conversationTitle,
  onMessageUpdate,
  resetRegeneratingState,
  onMediaTaskSubmitted,
}: ImageRegenParams): Promise<void> {
  const { tempUserMessage, tempAiMessage, tempUserId, newStreamingId } = createTempMessagePair(
    conversationId, userMessage, '图片生成中...'
  );

  setRegeneratingId(newStreamingId);
  setIsRegeneratingAI(true);
  setMessages((prev) => [...prev, tempUserMessage, tempAiMessage]);
  scrollToBottom();

  const userTimestamp = tempUserMessage.created_at;
  const originalImageParams = originalGenerationParams?.image;
  const savedSettings = getSavedSettings();
  const aspectRatio = originalImageParams?.aspectRatio ?? savedSettings.image.aspectRatio;
  const outputFormat = originalImageParams?.outputFormat ?? savedSettings.image.outputFormat;
  const resolution = originalImageParams?.resolution ?? savedSettings.image.resolution;

  const originalModel = originalImageParams?.model;
  const savedImageModel = modelId && getModelTypeById(modelId) === 'image' ? modelId : null;
  const currentImageModel = selectedModel?.type === 'image' ? selectedModel.id : null;
  const imageModelId = originalModel || savedImageModel || currentImageModel || MEDIA_DEFAULTS.IMAGE_MODEL;

  const modelConfig = ALL_MODELS.find((m) => m.id === imageModelId);
  const supportsResolution = modelConfig?.supportsResolution ?? false;
  const finalResolution = supportsResolution ? resolution : undefined;

  const imageGenerationParams: GenerationParams = {
    image: { aspectRatio, resolution: finalResolution, outputFormat, model: imageModelId },
  };

  try {
    await saveUserMessage(conversationId, userMessage, tempUserId, setMessages, userTimestamp);

    const response = userMessage.image_url
      ? await editImage({ prompt: userMessage.content, image_urls: [userMessage.image_url], size: aspectRatio, output_format: outputFormat, wait_for_result: false })
      : await generateImage({ prompt: userMessage.content, model: imageModelId as ImageModel, size: aspectRatio, output_format: outputFormat, resolution: finalResolution, wait_for_result: false });

    const successContent = userMessage.image_url ? '图片编辑完成' : '图片已生成完成';

    if (response.status === 'pending' || response.status === 'processing') {
      handleMediaPolling(response.task_id, newStreamingId, response.credits_consumed, {
        type: 'image',
        placeholderText: '正在生成图片...',
        successContent,
        pollInterval: 2000,
        userMessageTimestamp: userTimestamp,
        generationParams: imageGenerationParams,
        pollFn: getImageTaskStatus,
        extractUrl: (r) => ({ image_url: (r as { image_urls: string[] }).image_urls[0] }),
      }, conversationId, conversationTitle, setMessages, onMessageUpdate, resetRegeneratingState, onMediaTaskSubmitted);
    } else if (response.status === 'success' && response.image_urls.length > 0) {
      const aiCreatedAt = new Date(new Date(userTimestamp).getTime() + 1).toISOString();
      const savedMsg = await createMessage(conversationId, { content: successContent, role: 'assistant', image_url: response.image_urls[0], credits_cost: response.credits_consumed, created_at: aiCreatedAt, generation_params: imageGenerationParams });
      setMessages((prev) => prev.map((m) => (m.id === newStreamingId ? savedMsg : m)));
      resetRegeneratingState();
      onMediaTaskSubmitted?.();
      if (onMessageUpdate) onMessageUpdate(savedMsg.content);
    } else {
      throw new Error('图片生成失败');
    }
  } catch (error) {
    const errorCreatedAt = new Date(new Date(tempUserMessage.created_at).getTime() + 1).toISOString();
    await handleRegenError(error, conversationId, newStreamingId, 'image', setMessages, errorCreatedAt, imageGenerationParams);
    resetRegeneratingState();
    onMediaTaskSubmitted?.();
  }
}

/** 视频重新生成参数 */
interface VideoRegenParams {
  conversationId: string;
  userMessage: Message;
  originalGenerationParams?: GenerationParams | null;
  modelId?: string | null;
  selectedModel?: UnifiedModel | null;
  setMessages: React.Dispatch<React.SetStateAction<Message[]>>;
  scrollToBottom: (smooth?: boolean) => void;
  setRegeneratingId: (id: string | null) => void;
  setIsRegeneratingAI: (value: boolean) => void;
  conversationTitle: string;
  onMessageUpdate?: (newLastMessage: string) => void;
  resetRegeneratingState: () => void;
  onMediaTaskSubmitted?: () => void;
}

/** 执行视频重新生成 */
export async function executeVideoRegeneration({
  conversationId,
  userMessage,
  originalGenerationParams,
  modelId,
  selectedModel,
  setMessages,
  scrollToBottom,
  setRegeneratingId,
  setIsRegeneratingAI,
  conversationTitle,
  onMessageUpdate,
  resetRegeneratingState,
  onMediaTaskSubmitted,
}: VideoRegenParams): Promise<void> {
  const { tempUserMessage, tempAiMessage, tempUserId, newStreamingId } = createTempMessagePair(
    conversationId, userMessage, '视频生成中...'
  );

  setRegeneratingId(newStreamingId);
  setIsRegeneratingAI(true);
  setMessages((prev) => [...prev, tempUserMessage, tempAiMessage]);
  scrollToBottom();

  const userTimestamp = tempUserMessage.created_at;
  const originalVideoParams = originalGenerationParams?.video;
  const savedSettings = getSavedSettings();
  const videoFrames = originalVideoParams?.frames ?? savedSettings.video.frames;
  const videoAspectRatio = originalVideoParams?.aspectRatio ?? savedSettings.video.aspectRatio;
  const removeWatermark = originalVideoParams?.removeWatermark ?? savedSettings.video.removeWatermark;

  const originalModel = originalVideoParams?.model;
  const savedVideoModel = modelId && getModelTypeById(modelId) === 'video' ? modelId : null;
  const currentVideoModel = selectedModel?.type === 'video' ? selectedModel.id : null;
  const videoModelId = originalModel || savedVideoModel || currentVideoModel || MEDIA_DEFAULTS.VIDEO_MODEL;

  const savedModel = savedVideoModel ? ALL_MODELS.find((m) => m.id === savedVideoModel) : null;
  const savedSupportsI2V = savedModel?.type === 'video' && savedModel.capabilities.imageToVideo;
  const currentSupportsI2V = selectedModel?.type === 'video' && selectedModel.capabilities.imageToVideo;
  const i2vModelId = originalModel || (savedSupportsI2V ? savedVideoModel : null) || (currentSupportsI2V ? selectedModel!.id : null) || MEDIA_DEFAULTS.I2V_MODEL;

  const finalModelId = userMessage.image_url ? i2vModelId : videoModelId;
  const videoModelConfig = ALL_MODELS.find((m) => m.id === finalModelId);
  const supportedFrames = videoModelConfig?.videoPricing ? Object.keys(videoModelConfig.videoPricing) : ['10', '15'];
  const finalFrames = supportedFrames.includes(videoFrames) ? videoFrames : (supportedFrames[supportedFrames.length - 1] as typeof videoFrames);

  const videoGenerationParams: GenerationParams = {
    video: { frames: finalFrames, aspectRatio: videoAspectRatio, removeWatermark, model: finalModelId },
  };

  try {
    await saveUserMessage(conversationId, userMessage, tempUserId, setMessages, userTimestamp);

    const response = userMessage.image_url
      ? await generateImageToVideo({ prompt: userMessage.content, image_url: userMessage.image_url, model: i2vModelId as VideoModel, n_frames: finalFrames, aspect_ratio: videoAspectRatio, remove_watermark: removeWatermark, wait_for_result: false })
      : await generateTextToVideo({ prompt: userMessage.content, model: videoModelId as VideoModel, n_frames: finalFrames, aspect_ratio: videoAspectRatio, remove_watermark: removeWatermark, wait_for_result: false });

    const successContent = userMessage.image_url ? '视频生成完成（图生视频）' : '视频生成完成';

    if (response.status === 'pending' || response.status === 'processing') {
      handleMediaPolling(response.task_id, newStreamingId, response.credits_consumed, {
        type: 'video',
        placeholderText: '正在生成视频...',
        successContent,
        pollInterval: 5000,
        userMessageTimestamp: userTimestamp,
        generationParams: videoGenerationParams,
        pollFn: getVideoTaskStatus,
        extractUrl: (r) => ({ video_url: (r as { video_url: string }).video_url }),
      }, conversationId, conversationTitle, setMessages, onMessageUpdate, resetRegeneratingState, onMediaTaskSubmitted);
    } else if (response.status === 'success' && response.video_url) {
      const aiCreatedAt = new Date(new Date(userTimestamp).getTime() + 1).toISOString();
      const savedMsg = await createMessage(conversationId, { content: successContent, role: 'assistant', video_url: response.video_url, credits_cost: response.credits_consumed, created_at: aiCreatedAt, generation_params: videoGenerationParams });
      setMessages((prev) => prev.map((m) => (m.id === newStreamingId ? savedMsg : m)));
      resetRegeneratingState();
      onMediaTaskSubmitted?.();
      if (onMessageUpdate) onMessageUpdate(savedMsg.content);
    } else {
      throw new Error('视频生成失败');
    }
  } catch (error) {
    const errorCreatedAt = new Date(new Date(tempUserMessage.created_at).getTime() + 1).toISOString();
    await handleRegenError(error, conversationId, newStreamingId, 'video', setMessages, errorCreatedAt, videoGenerationParams);
    resetRegeneratingState();
    onMediaTaskSubmitted?.();
  }
}
