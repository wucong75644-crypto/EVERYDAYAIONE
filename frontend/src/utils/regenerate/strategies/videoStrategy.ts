/**
 * 视频消息原地重新生成策略
 * 在原位置替换失败的视频消息
 * 复用 mediaGenerationCore 的核心逻辑
 */

import { type Message, type GenerationParams } from '../../../services/message';
import { executeVideoGenerationCore } from '../../../services/messageSender/mediaGenerationCore';
import { computeVideoGenerationParams } from '../../mediaRegeneration';

interface RegenerateVideoInPlaceOptions {
  messageId: string;
  conversationId: string;
  userMessage: Message;
  generationParams?: GenerationParams;
  conversationTitle?: string;
  setMessages: React.Dispatch<React.SetStateAction<Message[]>>;
  resetRegeneratingState: () => void;
  onSuccess?: (finalMessage: Message) => void;
}

export async function regenerateVideoInPlace({
  messageId,
  conversationId,
  userMessage,
  generationParams,
  conversationTitle,
  setMessages,
  resetRegeneratingState,
  onSuccess,
}: RegenerateVideoInPlaceOptions): Promise<void> {
  const aiTimestamp = new Date().toISOString();

  // 使用统一的参数计算逻辑
  const { generationParams: videoGenerationParams, finalModelId } = computeVideoGenerationParams(
    generationParams,
    undefined,
    undefined,
    !!userMessage.image_url
  );

  // 调用核心生成逻辑
  await executeVideoGenerationCore({
    conversationId,
    prompt: userMessage.content,
    imageUrl: userMessage.image_url,
    modelId: finalModelId,
    generationParams: videoGenerationParams,
    conversationTitle,
    messageTimestamp: aiTimestamp,
    placeholderId: messageId,
    callbacks: {
      onSuccess: (savedMessage) => {
        setMessages((prev) => prev.map((m) => (m.id === messageId ? savedMessage : m)));
        resetRegeneratingState();
        if (onSuccess) onSuccess(savedMessage);
      },
      onError: (errorMessage) => {
        setMessages((prev) => prev.map((m) => (m.id === messageId ? errorMessage : m)));
        resetRegeneratingState();
      },
      onTaskSubmitted: () => {
        resetRegeneratingState();
      },
    },
  });
}
