/**
 * 媒体占位符出现时的自动滚动
 *
 * 滚动策略：
 * 1. 占位符（如"图片生成中..."）出现时 → 滚动让其完全显示
 * 2. 占位符被替换为真实图片/视频时 → 不滚动，保持位置不变
 */

import { useEffect, useLayoutEffect, useRef } from 'react';
import type { Message } from '../../services/message';

interface UseMediaReplacementScrollOptions {
  messages: Message[];
  scrollToBottom: (smooth?: boolean) => void;
  hasScrolledForConversation: boolean;
  userScrolledAway: boolean;
}

export function useMediaReplacementScroll({
  messages,
  scrollToBottom,
  hasScrolledForConversation,
  userScrolledAway,
}: UseMediaReplacementScrollOptions) {
  const prevIsPlaceholderRef = useRef(false);
  const scrollToBottomRef = useRef(scrollToBottom);

  // 使用 useLayoutEffect 同步更新 ref，避免在渲染期间访问
  useLayoutEffect(() => {
    scrollToBottomRef.current = scrollToBottom;
  }, [scrollToBottom]);

  useEffect(() => {
    if (!hasScrolledForConversation || messages.length === 0) {
      prevIsPlaceholderRef.current = false;
      return;
    }

    const lastMessage = messages[messages.length - 1];
    const isPlaceholder = lastMessage.id.startsWith('streaming-') &&
      (lastMessage.content.includes('生成中') || lastMessage.content.includes('正在'));

    const wasPlaceholder = prevIsPlaceholderRef.current;
    prevIsPlaceholderRef.current = isPlaceholder;

    // 占位符刚出现时滚动（从非占位符变为占位符）
    if (!wasPlaceholder && isPlaceholder && !userScrolledAway) {
      // 使用双重 RAF 确保占位符 DOM 完全渲染
      requestAnimationFrame(() => {
        requestAnimationFrame(() => {
          scrollToBottomRef.current(true);
        });
      });
    }

    // 占位符被替换为真实内容时（从占位符变为非占位符）
    // 不做任何滚动，保持当前位置
  }, [messages, hasScrolledForConversation, userScrolledAway]);
}
