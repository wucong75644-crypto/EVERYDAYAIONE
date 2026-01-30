/**
 * 对话切换时的滚动管理
 * 负责保存旧对话滚动位置和重置滚动状态
 */

import { useLayoutEffect, useRef, type RefObject } from 'react';
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

  // 使用 useLayoutEffect 确保在 useMessageLoadingScroll 之前执行
  useLayoutEffect(() => {
    const prevId = prevConversationIdRef.current;
    if (conversationId !== prevId) {
      // 保存旧对话的滚动位置（仅当用户滚走时才保存）
      // 修复：只在 container 有效时处理滚动位置，避免 container 无效时错误清除
      if (prevId) {
        const container = containerRef.current;
        if (container) {
          if (userScrolledAway) {
            setScrollPosition(prevId, container.scrollTop);
          } else {
            clearScrollPosition(prevId);
          }
        }
        // 如果 container 无效，保留之前的滚动位置（不做任何操作）
      }

      // 重置滚动状态
      resetScrollState();
      onConversationSwitch();
      prevConversationIdRef.current = conversationId;
    }
  }, [conversationId, resetScrollState, userScrolledAway, setScrollPosition, clearScrollPosition, containerRef, onConversationSwitch]);
}
