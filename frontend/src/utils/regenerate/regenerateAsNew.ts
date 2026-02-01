/**
 * 成功重新生成：模拟用户重新发送
 *
 * - 聊天：需要流式处理，由外部传入 handleChatAsNew
 * - 图片/视频：直接调用 executeImageRegeneration / executeVideoRegeneration
 */

import toast from 'react-hot-toast';
import type { Message, GenerationParams } from '../../services/message';
import type { UnifiedModel } from '../../constants/models';
import {
  executeImageRegeneration,
  executeVideoRegeneration,
} from '../mediaRegeneration';
import type { MessageType } from './index';

export interface RegenerateAsNewOptions {
  type: MessageType;
  conversationId: string;
  userMessage: Message;

  // 状态管理
  setMessages: React.Dispatch<React.SetStateAction<Message[]>>;
  scrollToBottom: (smooth?: boolean) => void;
  setRegeneratingId: (id: string | null) => void;
  setIsRegeneratingAI: (value: boolean) => void;
  resetRegeneratingState: () => void;

  // 参数
  generationParams?: GenerationParams;
  conversationTitle?: string;
  modelId?: string | null;
  selectedModel?: UnifiedModel | null;

  // 回调
  onMessageUpdate?: (newLastMessage: string) => void;
  onMediaTaskSubmitted?: () => void;

  // 聊天专用：需要外部传入（涉及流式处理和 React Hooks）
  handleChatAsNew?: (userMessage: Message) => Promise<void>;
}

export async function regenerateAsNew(options: RegenerateAsNewOptions): Promise<void> {
  const {
    type,
    conversationId,
    userMessage,
    setMessages,
    scrollToBottom,
    setRegeneratingId,
    setIsRegeneratingAI,
    resetRegeneratingState,
    generationParams,
    conversationTitle,
    modelId,
    selectedModel,
    onMessageUpdate,
    onMediaTaskSubmitted,
    handleChatAsNew,
  } = options;

  try {
    switch (type) {
      case 'chat':
        // 聊天需要流式处理，由外部 hook 处理
        if (!handleChatAsNew) {
          throw new Error('缺少 handleChatAsNew 处理器');
        }
        await handleChatAsNew(userMessage);
        break;

      case 'image':
        // 直接调用图片重新生成执行函数
        await executeImageRegeneration({
          conversationId,
          userMessage,
          originalGenerationParams: generationParams,
          modelId,
          selectedModel,
          setMessages,
          scrollToBottom,
          setRegeneratingId,
          setIsRegeneratingAI,
          conversationTitle: conversationTitle || '',
          onMessageUpdate,
          resetRegeneratingState,
          onMediaTaskSubmitted,
        });
        break;

      case 'video':
        // 直接调用视频重新生成执行函数
        await executeVideoRegeneration({
          conversationId,
          userMessage,
          originalGenerationParams: generationParams,
          modelId,
          selectedModel,
          setMessages,
          scrollToBottom,
          setRegeneratingId,
          setIsRegeneratingAI,
          conversationTitle: conversationTitle || '',
          onMessageUpdate,
          resetRegeneratingState,
          onMediaTaskSubmitted,
        });
        break;
    }
  } catch (error) {
    const errorMsg = error instanceof Error ? error.message : '重新生成失败';
    toast.error(errorMsg);
    throw error;
  }
}
