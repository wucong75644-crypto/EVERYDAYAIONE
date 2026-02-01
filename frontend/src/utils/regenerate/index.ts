/**
 * 统一重新生成入口
 * 自动判断失败/成功，调用对应策略
 */

import { regenerateInPlace } from './regenerateInPlace';
import { regenerateAsNew } from './regenerateAsNew';
import type { Message, GenerationParams } from '../../services/message';
import type { UnifiedModel } from '../../constants/models';

export type MessageType = 'chat' | 'image' | 'video';

export interface RegenerateMessageOptions {
  // 消息信息
  messageId: string;
  conversationId: string;
  targetMessage: Message;
  userMessage: Message;

  // 状态管理
  setMessages: React.Dispatch<React.SetStateAction<Message[]>>;
  setRegeneratingId: (id: string | null) => void;
  setIsRegeneratingAI: (value: boolean) => void;
  scrollToBottom: (smooth?: boolean) => void;
  userScrolledAway: boolean;
  resetRegeneratingState: () => void;

  // 回调
  onSuccess?: (finalMessage: Message) => void;
  onError?: (error: string) => void;
  onMessageUpdate?: (newLastMessage: string) => void;
  onMediaTaskSubmitted?: () => void;

  // 类型特定参数
  generationParams?: GenerationParams;
  conversationTitle?: string;
  modelId?: string | null;
  selectedModel?: UnifiedModel | null;

  // 聊天成功重新生成需要的处理器（涉及流式处理）
  handleChatAsNew?: (userMessage: Message) => Promise<void>;
}

/**
 * 统一重新生成入口
 * 自动判断失败/成功，调用对应策略
 */
export async function regenerateMessage(options: RegenerateMessageOptions): Promise<void> {
  const { targetMessage } = options;

  // 1. 自动判断消息类型
  const type = determineMessageType(targetMessage);

  // 2. 根据是否失败选择策略
  const isError = targetMessage.is_error === true;

  if (isError) {
    // 失败消息：原地替换
    await regenerateInPlace({
      ...options,
      type,
    });
  } else {
    // 成功消息：模拟重新发送
    await regenerateAsNew({
      ...options,
      type,
    });
  }
}

/**
 * 判断消息类型（优先级：图片 > 视频 > 聊天）
 */
function determineMessageType(message: Message): MessageType {
  const hasImageUrl = !!message.image_url;
  const hasVideoUrl = !!message.video_url;
  const hasImageParams = !!message.generation_params?.image;
  const hasVideoParams = !!message.generation_params?.video;

  if (hasImageUrl || hasImageParams) return 'image';
  if (hasVideoUrl || hasVideoParams) return 'video';
  return 'chat';
}
