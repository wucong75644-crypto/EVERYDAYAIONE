/**
 * 消息加载完成后的滚动定位
 * 负责恢复保存的滚动位置或滚动到底部
 */

import { useEffect, useRef, type RefObject } from 'react';
import { useChatStore } from '../../stores/useChatStore';

interface UseMessageLoadingScrollOptions {
  conversationId: string | null;
  messagesLength: number;
  loading: boolean;
  containerRef: RefObject<HTMLDivElement | null>;
  hasScrolledForConversation: boolean;
  onScrollComplete: () => void;
}

export function useMessageLoadingScroll({
  conversationId,
  messagesLength,
  loading,
  containerRef,
  hasScrolledForConversation,
  onScrollComplete,
}: UseMessageLoadingScrollOptions) {
  const prevLoadingRef = useRef(true);
  const getScrollPosition = useChatStore((state) => state.getScrollPosition);
  const hasUnreadMessages = useChatStore((state) => state.hasUnreadMessages);
  const clearConversationUnread = useChatStore((state) => state.clearConversationUnread);

  useEffect(() => {
    const wasLoading = prevLoadingRef.current;
    prevLoadingRef.current = loading;

    if (wasLoading && !loading && messagesLength > 0 && !hasScrolledForConversation && conversationId) {
      onScrollComplete();
      requestAnimationFrame(() => {
        const container = containerRef.current;
        if (!container) return;

        const hasUnread = hasUnreadMessages(conversationId);
        if (hasUnread) {
          container.scrollTop = container.scrollHeight;
          clearConversationUnread(conversationId);
        } else {
          const savedPosition = getScrollPosition(conversationId);
          if (savedPosition !== null) {
            const maxScroll = container.scrollHeight - container.clientHeight;
            container.scrollTop = Math.min(savedPosition, Math.max(0, maxScroll));
          } else {
            container.scrollTop = container.scrollHeight;
          }
        }
      });
    }
  }, [loading, messagesLength, conversationId, containerRef, getScrollPosition, hasUnreadMessages, clearConversationUnread, hasScrolledForConversation, onScrollComplete]);
}
