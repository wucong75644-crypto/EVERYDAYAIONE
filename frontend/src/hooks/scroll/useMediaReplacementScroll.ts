/**
 * 媒体内容替换时的自动滚动
 * 检测占位符被替换为真实内容并触发滚动
 */

import { useEffect, useRef } from 'react';
import type { Message } from '../../services/message';

interface UseMediaReplacementScrollOptions {
  messages: Message[];
  scrollToBottom: (smooth?: boolean) => void;
  hasScrolledForConversation: boolean;
}

interface LastMessageState {
  hasMedia: boolean;
  isError: boolean;
  isPlaceholder: boolean;
}

export function useMediaReplacementScroll({
  messages,
  scrollToBottom,
  hasScrolledForConversation,
}: UseMediaReplacementScrollOptions) {
  const prevLastMessageStateRef = useRef<LastMessageState | null>(null);
  const scrollToBottomRef = useRef(scrollToBottom);
  scrollToBottomRef.current = scrollToBottom;

  useEffect(() => {
    if (!hasScrolledForConversation || messages.length === 0) {
      prevLastMessageStateRef.current = null;
      return;
    }

    const lastMessage = messages[messages.length - 1];
    const isPlaceholder = lastMessage.id.startsWith('streaming-') &&
      (lastMessage.content.includes('生成中') || lastMessage.content.includes('正在'));
    const currentState: LastMessageState = {
      hasMedia: !!(lastMessage.image_url || lastMessage.video_url),
      isError: lastMessage.is_error === true,
      isPlaceholder,
    };

    const prevState = prevLastMessageStateRef.current;

    if (prevState && prevState.isPlaceholder && !currentState.isPlaceholder) {
      if (currentState.hasMedia || currentState.isError) {
        requestAnimationFrame(() => {
          scrollToBottomRef.current(true);
        });
      }
    }

    prevLastMessageStateRef.current = currentState;
  }, [messages, hasScrolledForConversation]);
}
