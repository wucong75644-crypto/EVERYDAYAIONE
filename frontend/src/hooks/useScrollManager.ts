/**
 * 滚动管理自定义Hook
 *
 * 封装消息列表的滚动逻辑，包括自动滚动、用户打断检测、滚动按钮等
 */

import { useState, useCallback, type RefObject } from 'react';

interface UseScrollManagerOptions {
  containerRef: RefObject<HTMLDivElement | null>;
  messagesEndRef: RefObject<HTMLDivElement | null>;
}

export function useScrollManager({ containerRef, messagesEndRef }: UseScrollManagerOptions) {
  const [showScrollButton, setShowScrollButton] = useState(false);
  const [userScrolledAway, setUserScrolledAway] = useState(false);

  // 滚动到底部
  const scrollToBottom = useCallback((smooth = true) => {
    if (messagesEndRef.current) {
      messagesEndRef.current.scrollIntoView({
        behavior: smooth ? 'smooth' : 'auto',
        block: 'end',
      });
    }
  }, [messagesEndRef]);

  // 处理滚动事件
  const handleScroll = useCallback(() => {
    const container = containerRef.current;
    if (!container) return;

    const { scrollTop, scrollHeight, clientHeight } = container;
    const distanceFromBottom = scrollHeight - scrollTop - clientHeight;
    const isAtBottom = distanceFromBottom < 100;

    // 用户主动向上滚动（打断自动滚动）
    if (!isAtBottom && distanceFromBottom > 200) {
      setUserScrolledAway(true);
    } else if (isAtBottom) {
      setUserScrolledAway(false);
    }

    // 显示/隐藏滚动按钮
    setShowScrollButton(distanceFromBottom > 300);
  }, [containerRef]);

  // 重置滚动状态（对话切换时调用）
  const resetScrollState = useCallback(() => {
    setUserScrolledAway(false);
    setShowScrollButton(false);
  }, []);

  return {
    showScrollButton,
    userScrolledAway,
    setUserScrolledAway,
    scrollToBottom,
    handleScroll,
    resetScrollState,
  };
}
