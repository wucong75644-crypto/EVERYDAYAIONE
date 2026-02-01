/**
 * 统一媒体消息处理 Hook
 *
 * 合并图片/视频处理器，消除重复代码
 * 根据 type 参数区分图片和视频生成
 */

import { type UnifiedModel } from '../../constants/models';
import { type Message } from '../../services/message';
import { sendMediaMessage } from '../../services/messageSender';
import { computeImageGenerationParams, computeVideoGenerationParams } from '../../utils/mediaRegeneration';

export type MediaType = 'image' | 'video';

interface UseMediaMessageHandlerParams {
  type: MediaType;
  selectedModel: UnifiedModel;

  // 图片参数
  aspectRatio?: string;
  outputFormat?: string;
  resolution?: string;

  // 视频参数
  videoFrames?: string;
  videoAspectRatio?: string;
  removeWatermark?: boolean;

  // 对话标题（媒体任务需要）
  conversationTitle?: string;

  // 回调
  onMessagePending: (message: Message) => void;
  onMessageSent: (aiMessage?: Message | null) => void;
  onMediaTaskSubmitted?: () => void;
}

export function useMediaMessageHandler(params: UseMediaMessageHandlerParams) {
  const { type, selectedModel, conversationTitle: defaultTitle, onMessagePending, onMessageSent, onMediaTaskSubmitted } = params;

  const handleMediaGeneration = async (
    conversationId: string,
    prompt: string,
    imageUrl: string | null = null,
    conversationTitle: string = defaultTitle ?? ''
  ) => {
    if (type === 'image') {
      // 图片生成
      const generationParams = computeImageGenerationParams(null, selectedModel.id, selectedModel);
      await sendMediaMessage({
        type: 'image',
        conversationId,
        content: prompt,
        imageUrl,
        modelId: selectedModel.id,
        generationParams,
        conversationTitle,
        callbacks: {
          onMessagePending,
          onMessageSent,
          onMediaTaskSubmitted,
        },
      });
    } else {
      // 视频生成
      const { generationParams, finalModelId } = computeVideoGenerationParams(
        null,
        selectedModel.id,
        selectedModel,
        !!imageUrl
      );
      await sendMediaMessage({
        type: 'video',
        conversationId,
        content: prompt,
        imageUrl,
        modelId: finalModelId,
        generationParams,
        conversationTitle,
        callbacks: {
          onMessagePending,
          onMessageSent,
          onMediaTaskSubmitted,
        },
      });
    }
  };

  return { handleMediaGeneration };
}
