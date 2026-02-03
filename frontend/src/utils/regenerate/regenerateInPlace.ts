/**
 * 失败重新生成：原地替换
 * 适用于所有类型（聊天、图片、视频）
 */

import toast from 'react-hot-toast';
import { regenerateChatInPlace } from './strategies/chatStrategy';
import { regenerateImageInPlace } from './strategies/imageStrategy';
import { regenerateVideoInPlace } from './strategies/videoStrategy';
import type { RegenerateMessageOptions, MessageType } from './index';

interface RegenerateInPlaceOptions extends RegenerateMessageOptions {
  type: MessageType;
}

export async function regenerateInPlace({
  messageId,
  conversationId,
  targetMessage,
  userMessage,
  type,
  setMessages,
  setRegeneratingId,
  setIsRegeneratingAI,
  onSuccess,
  onError,
  resetRegeneratingState,
  generationParams,
  conversationTitle,
}: RegenerateInPlaceOptions): Promise<void> {

  // 1. 统一的占位逻辑（清空内容，触发"正在生成"显示）
  setRegeneratingId(messageId);
  setIsRegeneratingAI(true);
  setMessages((prev) =>
    prev.map((m) => (m.id === messageId ? {
      ...m,
      content: '',
      is_error: false,
      image_url: null,
      video_url: null
    } : m))
  );

  try {
    // 2. 根据类型调用对应策略
    switch (type) {
      case 'chat':
        await regenerateChatInPlace({
          messageId,
          conversationId,
          setMessages,
          resetRegeneratingState,
          onSuccess,
        });
        break;

      case 'image':
        await regenerateImageInPlace({
          messageId,
          conversationId,
          userMessage,
          generationParams,
          conversationTitle,
          setMessages,
          resetRegeneratingState,
          onSuccess,
        });
        break;

      case 'video':
        await regenerateVideoInPlace({
          messageId,
          conversationId,
          userMessage,
          generationParams,
          conversationTitle,
          setMessages,
          resetRegeneratingState,
          onSuccess,
        });
        break;
    }
  } catch (error) {
    // 3. 统一错误处理（恢复原消息）
    setMessages((prev) => prev.map((m) => (m.id === messageId ? targetMessage : m)));
    resetRegeneratingState();

    const errorMsg = error instanceof Error ? error.message : '重新生成失败';
    onError?.(errorMsg);
    toast.error(errorMsg);
  }
}
