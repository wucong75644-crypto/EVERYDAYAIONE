/**
 * 新消息添加后的自动滚动
 * 检测长消息并智能滚动到合适位置
 */

import { useEffect, useRef, type RefObject } from 'react';
import type { Message } from '../../services/message';

const LONG_MESSAGE_RATIO = 0.8;

interface UseNewMessageScrollOptions {
  messages: Message[];
  containerRef: RefObject<HTMLDivElement | null>;
  userScrolledAway: boolean;
  setUserScrolledAway: (value: boolean) => void;
  scrollToBottomDebounced: (smooth?: boolean) => void;
  scrollToElement: (element: HTMLElement, position: 'top' | 'bottom') => void;
  hasScrolledForConversation: boolean;
  onMessageCountChange: (count: number) => void;
}

export function useNewMessageScroll({
  messages,
  containerRef,
  userScrolledAway,
  setUserScrolledAway,
  scrollToBottomDebounced,
  scrollToElement,
  hasScrolledForConversation,
  onMessageCountChange,
}: UseNewMessageScrollOptions) {
  const prevMessageCountRef = useRef(0);

  useEffect(() => {
    const currentCount = messages.length;
    const prevCount = prevMessageCountRef.current;

    if (currentCount > prevCount && prevCount > 0 && hasScrolledForConversation) {
      const newMessages = messages.slice(prevCount);
      const hasUserMessage = newMessages.some(
        (m) => m.role === 'user' || m.id.startsWith('temp-')
      );

      if (hasUserMessage) {
        setUserScrolledAway(false);
      }

      if (hasUserMessage || !userScrolledAway) {
        requestAnimationFrame(() => {
          const container = containerRef.current;
          if (!container) return;

          const messageElements = container.querySelectorAll('[data-message-id]');
          const lastMessageElement = messageElements[messageElements.length - 1] as HTMLElement | undefined;

          if (lastMessageElement) {
            const messageHeight = lastMessageElement.offsetHeight;
            const containerHeight = container.clientHeight;

            if (messageHeight > containerHeight * LONG_MESSAGE_RATIO) {
              scrollToElement(lastMessageElement, 'top');
              return;
            }
          }

          scrollToBottomDebounced(true);
        });
      }
    }

    prevMessageCountRef.current = currentCount;
    onMessageCountChange(currentCount);
  }, [messages, userScrolledAway, setUserScrolledAway, scrollToBottomDebounced, scrollToElement, containerRef, hasScrolledForConversation, onMessageCountChange]);
}
