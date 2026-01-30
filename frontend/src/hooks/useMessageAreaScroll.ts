/**
 * MessageArea 滚动行为管理 Hook（组合器）
 *
 * 组合多个独立的滚动管理 Hook，提供统一的滚动行为接口
 */

import { useRef, useCallback, useLayoutEffect, useState, type RefObject } from 'react';
import type { Message } from '../services/message';
import { useConversationSwitchScroll } from './scroll/useConversationSwitchScroll';
import { useMessageLoadingScroll } from './scroll/useMessageLoadingScroll';
import { useNewMessageScroll } from './scroll/useNewMessageScroll';
import { useStreamingScroll } from './scroll/useStreamingScroll';
import { useMediaReplacementScroll } from './scroll/useMediaReplacementScroll';

interface UseMessageAreaScrollOptions {
  conversationId: string | null;
  messages: Message[];
  loading: boolean;
  containerRef: RefObject<HTMLDivElement | null>;
  userScrolledAway: boolean;
  setUserScrolledAway: (value: boolean) => void;
  scrollToBottom: (smooth?: boolean) => void;
  scrollToBottomDebounced: (smooth?: boolean) => void;
  scrollToElement: (element: HTMLElement, position: 'top' | 'bottom') => void;
  resetScrollState: () => void;
  runtimeState?: {
    streamingMessageId: string | null;
    optimisticMessages: Message[];
  };
}

export function useMessageAreaScroll(options: UseMessageAreaScrollOptions) {
  const {
    conversationId,
    messages,
    loading,
    containerRef,
    userScrolledAway,
    setUserScrolledAway,
    scrollToBottom,
    scrollToBottomDebounced,
    scrollToElement,
    resetScrollState,
    runtimeState,
  } = options;

  // 滚动状态跟踪 - 使用 state 替代 ref 以符合 React 规范
  const [hasScrolledForConversation, setHasScrolledForConversation] = useState(false);
  const scrollToBottomRef = useRef(scrollToBottom);

  // 使用 useLayoutEffect 同步更新 ref，避免在渲染期间访问
  useLayoutEffect(() => {
    scrollToBottomRef.current = scrollToBottom;
  }, [scrollToBottom]);

  // 对话切换时的滚动管理
  useConversationSwitchScroll({
    conversationId,
    containerRef,
    userScrolledAway,
    resetScrollState,
    onConversationSwitch: () => {
      setHasScrolledForConversation(false);
    },
  });

  // 消息加载完成后的滚动定位
  useMessageLoadingScroll({
    conversationId,
    messagesLength: messages.length,
    loading,
    containerRef,
    hasScrolledForConversation,
    onScrollComplete: () => {
      setHasScrolledForConversation(true);
    },
  });

  // 新消息添加后的自动滚动
  useNewMessageScroll({
    messages,
    containerRef,
    userScrolledAway,
    setUserScrolledAway,
    scrollToBottomDebounced,
    scrollToElement,
    hasScrolledForConversation,
    onMessageCountChange: () => {
      // 消息数量变化时的额外处理（如果需要）
    },
  });

  // 流式内容更新时的自动滚动
  useStreamingScroll({
    runtimeState,
    userScrolledAway,
    scrollToBottom,
    hasScrolledForConversation,
  });

  // 媒体内容替换时的自动滚动
  useMediaReplacementScroll({
    messages,
    scrollToBottom,
    hasScrolledForConversation,
    userScrolledAway,
  });

  // 重新生成开始时的滚动处理
  const handleRegenerateScroll = useCallback((regeneratingId: string | null, isRegeneratingAI: boolean) => {
    if (isRegeneratingAI && regeneratingId) {
      requestAnimationFrame(() => {
        if (!userScrolledAway) {
          const isFailedMessageRegenerate = messages.some(
            m => m.id === regeneratingId && m.content === ''
          );
          scrollToBottomRef.current(!isFailedMessageRegenerate);
        }
      });
    }
  }, [userScrolledAway, messages]);

  return {
    hasScrolledForConversation,
    handleRegenerateScroll,
  };
}
