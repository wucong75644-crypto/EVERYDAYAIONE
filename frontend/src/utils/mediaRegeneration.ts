/**
 * 媒体重新生成工具
 *
 * 包含图片/视频重新生成的参数计算逻辑
 * 核心发送逻辑复用 messageSender
 */

import { type Message, type GenerationParams } from '../services/message';
import { type UnifiedModel, ALL_MODELS } from '../constants/models';
import { getSavedSettings } from './settingsStorage';
import { sendMediaMessage, type ImageSenderParams, type VideoSenderParams } from '../services/messageSender';
import { useConversationRuntimeStore } from '../stores/useConversationRuntimeStore';

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

/**
 * 计算图片生成参数
 * 优先级：原始参数 > 保存的设置 > 默认值
 */
export function computeImageGenerationParams(
  originalParams?: GenerationParams | null,
  modelId?: string | null,
  selectedModel?: UnifiedModel | null
): ImageSenderParams['generationParams'] {
  const originalImageParams = originalParams?.image;
  const savedSettings = getSavedSettings();

  const aspectRatio = originalImageParams?.aspectRatio ?? savedSettings.image.aspectRatio;
  const outputFormat = originalImageParams?.outputFormat ?? savedSettings.image.outputFormat;
  const resolution = originalImageParams?.resolution ?? savedSettings.image.resolution;

  const originalModel = originalImageParams?.model;
  const savedImageModel = modelId && getModelTypeById(modelId) === 'image' ? modelId : null;
  const currentImageModel = selectedModel?.type === 'image' ? selectedModel.id : null;
  const imageModelId = originalModel || savedImageModel || currentImageModel || MEDIA_DEFAULTS.IMAGE_MODEL;

  return {
    image: {
      aspectRatio,
      resolution,
      outputFormat,
      model: imageModelId,
    },
  };
}

/**
 * 计算视频生成参数
 * 优先级：原始参数 > 保存的设置 > 默认值
 */
export function computeVideoGenerationParams(
  originalParams?: GenerationParams | null,
  modelId?: string | null,
  selectedModel?: UnifiedModel | null,
  hasImage?: boolean
): { generationParams: VideoSenderParams['generationParams']; finalModelId: string } {
  const originalVideoParams = originalParams?.video;
  const savedSettings = getSavedSettings();

  const videoFrames = originalVideoParams?.frames ?? savedSettings.video.frames;
  const videoAspectRatio = originalVideoParams?.aspectRatio ?? savedSettings.video.aspectRatio;
  const removeWatermark = originalVideoParams?.removeWatermark ?? savedSettings.video.removeWatermark;

  const originalModel = originalVideoParams?.model;
  const savedVideoModel = modelId && getModelTypeById(modelId) === 'video' ? modelId : null;
  const currentVideoModel = selectedModel?.type === 'video' ? selectedModel.id : null;
  const videoModelId = originalModel || savedVideoModel || currentVideoModel || MEDIA_DEFAULTS.VIDEO_MODEL;

  // 图生视频模型选择
  const savedModel = savedVideoModel ? ALL_MODELS.find((m) => m.id === savedVideoModel) : null;
  const savedSupportsI2V = savedModel?.type === 'video' && savedModel.capabilities.imageToVideo;
  const currentSupportsI2V = selectedModel?.type === 'video' && selectedModel.capabilities.imageToVideo;
  const i2vModelId = originalModel || (savedSupportsI2V ? savedVideoModel : null) || (currentSupportsI2V ? selectedModel!.id : null) || MEDIA_DEFAULTS.I2V_MODEL;

  const finalModelId = hasImage ? i2vModelId : videoModelId;

  // 帧数兼容性检查
  const videoModelConfig = ALL_MODELS.find((m) => m.id === finalModelId);
  const supportedFrames = videoModelConfig?.videoPricing ? Object.keys(videoModelConfig.videoPricing) : ['10', '15'];
  const finalFrames = supportedFrames.includes(videoFrames) ? videoFrames : (supportedFrames[supportedFrames.length - 1] as typeof videoFrames);

  return {
    generationParams: {
      video: {
        frames: finalFrames,
        aspectRatio: videoAspectRatio,
        removeWatermark,
        model: finalModelId,
      },
    },
    finalModelId,
  };
}

/**
 * 创建媒体重新生成回调（复用模式）
 * 用于成功消息重新生成场景
 *
 * 缓存写入路径：
 * - 临时消息（temp-xxx、streaming-xxx）→ RuntimeStore（占位符管理）
 * - 持久化消息 → setMessages 兼容层 → ChatStore
 */
export function createMediaRegenCallbacks(
  setMessages: React.Dispatch<React.SetStateAction<Message[]>>,
  resetRegeneratingState: () => void,
  onMessageUpdate?: (newLastMessage: string) => void,
  onMediaTaskSubmitted?: () => void,
  // RuntimeStore 操作方法（统一占位符管理）
  addOptimisticUserMessage?: (conversationId: string, message: Message) => void,
  addMediaPlaceholder?: (conversationId: string, placeholder: Message) => void
) {
  return {
    onMessagePending: (msg: Message) => {
      // 临时消息写入 RuntimeStore（保持占位符管理一致性）
      if (msg.id.startsWith('temp-') && addOptimisticUserMessage && msg.conversation_id) {
        addOptimisticUserMessage(msg.conversation_id, msg);
      } else if (msg.id.startsWith('streaming-') && addMediaPlaceholder && msg.conversation_id) {
        addMediaPlaceholder(msg.conversation_id, msg);
      } else {
        // 持久化消息通过 setMessages 兼容层
        setMessages((prev) => {
          const existing = prev.findIndex((m) => m.id === msg.id);
          if (existing >= 0) {
            return prev.map((m, i) => (i === existing ? msg : m));
          }
          return [...prev, msg];
        });
      }
    },
    onMessageSent: (aiMessage?: Message | null) => {
      resetRegeneratingState();
      // 持久化消息统一走 setMessages 兼容层
      if (aiMessage) {
        setMessages((prev) => [...prev, aiMessage]);
      }
      if (aiMessage && !aiMessage.is_error && onMessageUpdate) {
        onMessageUpdate(aiMessage.content);
      }
    },
    onMediaTaskSubmitted: () => {
      resetRegeneratingState();
      onMediaTaskSubmitted?.();
    },
  };
}

/** 媒体重新生成公共参数 */
interface MediaRegenParams {
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

/** 执行图片重新生成 - 复用 sendMediaMessage */
export async function executeImageRegeneration({
  conversationId,
  userMessage,
  originalGenerationParams,
  modelId,
  selectedModel,
  setMessages,
  scrollToBottom,
  setIsRegeneratingAI,
  conversationTitle,
  onMessageUpdate,
  resetRegeneratingState,
  onMediaTaskSubmitted,
}: MediaRegenParams): Promise<void> {
  setIsRegeneratingAI(true);
  scrollToBottom();

  const generationParams = computeImageGenerationParams(originalGenerationParams, modelId, selectedModel);

  // 获取 RuntimeStore 方法（统一占位符管理）
  const { addOptimisticUserMessage, addMediaPlaceholder } = useConversationRuntimeStore.getState();

  await sendMediaMessage({
    type: 'image',
    conversationId,
    content: userMessage.content,
    imageUrl: userMessage.image_url,
    modelId: generationParams.image.model,
    generationParams,
    conversationTitle,
    callbacks: createMediaRegenCallbacks(
      setMessages,
      resetRegeneratingState,
      onMessageUpdate,
      onMediaTaskSubmitted,
      addOptimisticUserMessage,
      addMediaPlaceholder
    ),
  });
}

/** 执行视频重新生成 - 复用 sendMediaMessage */
export async function executeVideoRegeneration({
  conversationId,
  userMessage,
  originalGenerationParams,
  modelId,
  selectedModel,
  setMessages,
  scrollToBottom,
  setIsRegeneratingAI,
  conversationTitle,
  onMessageUpdate,
  resetRegeneratingState,
  onMediaTaskSubmitted,
}: MediaRegenParams): Promise<void> {
  setIsRegeneratingAI(true);
  scrollToBottom();

  const { generationParams, finalModelId } = computeVideoGenerationParams(
    originalGenerationParams,
    modelId,
    selectedModel,
    !!userMessage.image_url
  );

  // 获取 RuntimeStore 方法（统一占位符管理）
  const { addOptimisticUserMessage, addMediaPlaceholder } = useConversationRuntimeStore.getState();

  await sendMediaMessage({
    type: 'video',
    conversationId,
    content: userMessage.content,
    imageUrl: userMessage.image_url,
    modelId: finalModelId,
    generationParams,
    conversationTitle,
    callbacks: createMediaRegenCallbacks(
      setMessages,
      resetRegeneratingState,
      onMessageUpdate,
      onMediaTaskSubmitted,
      addOptimisticUserMessage,
      addMediaPlaceholder
    ),
  });
}
