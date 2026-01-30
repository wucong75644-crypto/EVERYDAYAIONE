/**
 * 消息加载完成后的滚动定位
 * 负责恢复保存的滚动位置或滚动到底部
 */

import { useLayoutEffect, useRef, type RefObject } from 'react';
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
  // 使用内部 ref 追踪已完成滚动的对话 ID，避免依赖外部状态更新的时序问题
  const scrolledConversationRef = useRef<string | null>(null);
  const getScrollPosition = useChatStore((state) => state.getScrollPosition);
  const hasUnreadMessages = useChatStore((state) => state.hasUnreadMessages);
  const clearConversationUnread = useChatStore((state) => state.clearConversationUnread);

  useLayoutEffect(() => {
    // 条件：
    // 1. 有对话 ID
    // 2. 消息已加载（messagesLength > 0）
    // 3. 不在加载中
    // 4. 还没有为当前对话执行过滚动（使用内部 ref 判断）
    if (!conversationId || messagesLength === 0 || loading) {
      return;
    }

    // 如果已经为当前对话执行过滚动，跳过
    if (scrolledConversationRef.current === conversationId) {
      return;
    }

    const container = containerRef.current;
    if (!container) {
      return;
    }

    // 使用双重 requestAnimationFrame 确保 DOM 完全渲染
    // 第一个 RAF 等待当前帧完成，第二个 RAF 确保布局计算完成
    requestAnimationFrame(() => {
      requestAnimationFrame(() => {
        const currentContainer = containerRef.current;
        if (!currentContainer) return;

        // 再次检查是否已经滚动过（防止竞态）
        if (scrolledConversationRef.current === conversationId) return;

        // 执行滚动定位
        const hasUnread = hasUnreadMessages(conversationId);
        const savedPosition = getScrollPosition(conversationId);
        const maxScroll = currentContainer.scrollHeight - currentContainer.clientHeight;

        if (hasUnread) {
          currentContainer.scrollTop = currentContainer.scrollHeight;
          clearConversationUnread(conversationId);
        } else if (savedPosition !== null) {
          currentContainer.scrollTop = Math.min(savedPosition, Math.max(0, maxScroll));
        } else {
          currentContainer.scrollTop = currentContainer.scrollHeight;
        }

        // 标记当前对话已完成滚动
        scrolledConversationRef.current = conversationId;

        // 通知外部状态更新
        onScrollComplete();
      });
    });
  }, [conversationId, messagesLength, loading, containerRef, getScrollPosition, hasUnreadMessages, clearConversationUnread, onScrollComplete]);

  // 当外部状态被重置时（对话切换），同步重置内部 ref
  useLayoutEffect(() => {
    if (!hasScrolledForConversation && scrolledConversationRef.current !== null) {
      scrolledConversationRef.current = null;
    }
  }, [hasScrolledForConversation]);
}
