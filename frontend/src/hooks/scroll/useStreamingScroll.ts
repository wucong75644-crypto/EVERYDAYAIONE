/**
 * 流式内容更新时的自动滚动
 * AI 输出时持续跟随滚动
 */

import { useEffect, useLayoutEffect, useRef } from 'react';
import type { Message } from '../../services/message';

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
      requestAnimationFrame(() => {
        scrollToBottomRef.current(false);
      });
    }

    prevStreamingContentLengthRef.current = currentLength;

    if (!runtimeState?.streamingMessageId) {
      prevStreamingContentLengthRef.current = 0;
    }
  }, [runtimeState?.streamingMessageId, runtimeState?.optimisticMessages, userScrolledAway, hasScrolledForConversation]);
}
