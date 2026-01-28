/**
 * 对话切换时的滚动管理
 * 负责保存旧对话滚动位置和重置滚动状态
 */

import { useEffect, useRef, type RefObject } from 'react';
import { useChatStore } from '../../stores/useChatStore';

interface UseConversationSwitchScrollOptions {
  conversationId: string | null;
  containerRef: RefObject<HTMLDivElement | null>;
  userScrolledAway: boolean;
  resetScrollState: () => void;
  onConversationSwitch: () => void;
}

export function useConversationSwitchScroll({
  conversationId,
  containerRef,
  userScrolledAway,
  resetScrollState,
  onConversationSwitch,
}: UseConversationSwitchScrollOptions) {
  const prevConversationIdRef = useRef<string | null>(null);
  const setScrollPosition = useChatStore((state) => state.setScrollPosition);
  const clearScrollPosition = useChatStore((state) => state.clearScrollPosition);

  useEffect(() => {
    const prevId = prevConversationIdRef.current;
    if (conversationId !== prevId) {
      // 保存旧对话的滚动位置（仅当用户滚走时才保存）
      if (prevId) {
        const container = containerRef.current;
        if (container && userScrolledAway) {
          setScrollPosition(prevId, container.scrollTop);
        } else if (prevId) {
          clearScrollPosition(prevId);
        }
      }

      // 重置滚动状态
      resetScrollState();
      onConversationSwitch();
      prevConversationIdRef.current = conversationId;
    }
  }, [conversationId, resetScrollState, userScrolledAway, setScrollPosition, clearScrollPosition, containerRef, onConversationSwitch]);
}
