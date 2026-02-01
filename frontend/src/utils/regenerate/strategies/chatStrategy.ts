/**
 * 聊天消息原地重新生成策略
 * 调用 regenerateMessageStream API，在原位置替换消息
 */

import { regenerateMessageStream, type Message } from '../../../services/message';

interface RegenerateChatInPlaceOptions {
  messageId: string;
  conversationId: string;
  setMessages: React.Dispatch<React.SetStateAction<Message[]>>;
  scrollToBottom: (smooth?: boolean) => void;
  userScrolledAway: boolean;
  resetRegeneratingState: () => void;
  onSuccess?: (finalMessage: Message) => void;
}

export async function regenerateChatInPlace({
  messageId,
  conversationId,
  setMessages,
  scrollToBottom,
  userScrolledAway,
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
      if (!userScrolledAway) scrollToBottom();
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
