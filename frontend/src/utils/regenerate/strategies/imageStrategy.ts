/**
 * 图片消息原地重新生成策略
 * 在原位置替换失败的图片消息
 * 复用 mediaGenerationCore 的核心逻辑
 */

import { type Message, type GenerationParams } from '../../../services/message';
import { executeImageGenerationCore } from '../../../services/messageSender/mediaGenerationCore';
import { computeImageGenerationParams } from '../../mediaRegeneration';

interface RegenerateImageInPlaceOptions {
  messageId: string;
  conversationId: string;
  userMessage: Message;
  generationParams?: GenerationParams;
  conversationTitle?: string;
  setMessages: React.Dispatch<React.SetStateAction<Message[]>>;
  resetRegeneratingState: () => void;
  onSuccess?: (finalMessage: Message) => void;
}

export async function regenerateImageInPlace({
  messageId,
  conversationId,
  userMessage,
  generationParams,
  conversationTitle,
  setMessages,
  resetRegeneratingState,
  onSuccess,
}: RegenerateImageInPlaceOptions): Promise<void> {
  const aiTimestamp = new Date().toISOString();

  // 使用统一的参数计算逻辑
  const imageGenerationParams = computeImageGenerationParams(generationParams);

  // 调用核心生成逻辑
  await executeImageGenerationCore({
    conversationId,
    prompt: userMessage.content,
    imageUrl: userMessage.image_url,
    modelId: imageGenerationParams.image.model,
    generationParams: imageGenerationParams,
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
