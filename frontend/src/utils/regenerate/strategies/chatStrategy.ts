/**
 * 聊天消息原地重新生成策略
 * 调用 regenerateMessageStream API，在原位置替换消息
 */

import { regenerateMessageStream, type Message } from '../../../services/message';

interface RegenerateChatInPlaceOptions {
  messageId: string;
  conversationId: string;
  setMessages: React.Dispatch<React.SetStateAction<Message[]>>;
  resetRegeneratingState: () => void;
  onSuccess?: (finalMessage: Message) => void;
}

export async function regenerateChatInPlace({
  messageId,
  conversationId,
  setMessages,
  resetRegeneratingState,
  onSuccess,
}: RegenerateChatInPlaceOptions): Promise<void> {
  const regenConvId = conversationId;
  const contentRef = { current: '' };

  await regenerateMessageStream(conversationId, messageId, {
    onContent: (content: string) => {
      contentRef.current += content;
      setMessages((prev) => {
        if (conversationId !== regenConvId) return prev;
        return prev.map((m) =>
          m.id === messageId ? { ...m, content: contentRef.current, is_error: false } : m
        );
      });
      // 流式中不调用 scrollToBottom，由 Virtua shift 模式自动维护底部位置
    },
    onDone: (finalMessage: Message | null) => {
      if (!finalMessage) return;
      setMessages((prev) => {
        if (conversationId !== regenConvId) return prev;
        return prev.map((m) => (m.id === messageId ? finalMessage : m));
      });
      resetRegeneratingState();
      if (onSuccess) onSuccess(finalMessage);
    },
    onError: (error: string) => {
      resetRegeneratingState();
      throw new Error(error);
    },
  });
}
