/**
 * 流式内容更新时的自动滚动
 * AI 输出时持续跟随滚动
 */

import { useEffect, useLayoutEffect, useRef } from 'react';
import type { Message } from '../../services/message';

/** 流式滚动节流间隔（ms）- 避免过于频繁的滚动导致抖动 */
const STREAMING_SCROLL_THROTTLE_MS = 100;

interface UseStreamingScrollOptions {
  runtimeState?: {
    streamingMessageId: string | null;
    optimisticMessages: Message[];
  };
  userScrolledAway: boolean;
  scrollToBottom: (smooth?: boolean) => void;
  hasScrolledForConversation: boolean;
}

export function useStreamingScroll({
  runtimeState,
  userScrolledAway,
  scrollToBottom,
  hasScrolledForConversation,
}: UseStreamingScrollOptions) {
  const prevStreamingContentLengthRef = useRef(0);
  const scrollToBottomRef = useRef(scrollToBottom);
  const lastScrollTimeRef = useRef(0);
  const pendingScrollRef = useRef<number | null>(null);

  // 使用 useLayoutEffect 同步更新 ref，避免在渲染期间访问
  useLayoutEffect(() => {
    scrollToBottomRef.current = scrollToBottom;
  }, [scrollToBottom]);

  useEffect(() => {
    const streamingMessage = runtimeState?.streamingMessageId
      ? runtimeState.optimisticMessages.find(m => m.id === runtimeState.streamingMessageId)
      : null;
    const currentLength = streamingMessage?.content.length ?? 0;
    const prevLength = prevStreamingContentLengthRef.current;

    if (currentLength > prevLength && prevLength > 0 && !userScrolledAway && hasScrolledForConversation) {
      const now = Date.now();
      const timeSinceLastScroll = now - lastScrollTimeRef.current;

      // 节流：如果距离上次滚动时间不够，延迟执行
      if (timeSinceLastScroll >= STREAMING_SCROLL_THROTTLE_MS) {
        // 立即执行滚动
        lastScrollTimeRef.current = now;
        requestAnimationFrame(() => {
          scrollToBottomRef.current(false);
        });
      } else if (pendingScrollRef.current === null) {
        // 设置延迟滚动，确保最终状态正确
        const delay = STREAMING_SCROLL_THROTTLE_MS - timeSinceLastScroll;
        pendingScrollRef.current = window.setTimeout(() => {
          lastScrollTimeRef.current = Date.now();
          pendingScrollRef.current = null;
          requestAnimationFrame(() => {
            scrollToBottomRef.current(false);
          });
        }, delay);
      }
    }

    prevStreamingContentLengthRef.current = currentLength;

    if (!runtimeState?.streamingMessageId) {
      prevStreamingContentLengthRef.current = 0;
      // 清理待处理的滚动
      if (pendingScrollRef.current !== null) {
        clearTimeout(pendingScrollRef.current);
        pendingScrollRef.current = null;
      }
    }
  }, [runtimeState?.streamingMessageId, runtimeState?.optimisticMessages, userScrolledAway, hasScrolledForConversation]);

  // 组件卸载时清理
  useEffect(() => {
    return () => {
      if (pendingScrollRef.current !== null) {
        clearTimeout(pendingScrollRef.current);
      }
    };
  }, []);
}
