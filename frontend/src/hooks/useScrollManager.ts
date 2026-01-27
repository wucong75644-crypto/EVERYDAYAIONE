/**
 * 滚动管理自定义Hook（重构版）
 *
 * 统一管理消息列表的滚动逻辑：
 * - 智能滚动（用户未打断时自动滚动）
 * - 用户打断检测（向上滚动超过阈值）
 * - 新消息通知
 * - 滚动按钮显示/隐藏
 *
 * 设计原则（参考 ChatGPT/Claude.ai）：
 * 1. 用户主动上滑超过阈值 → 停止自动滚动，显示"回到底部"按钮
 * 2. 有新内容且用户已滚走 → 显示"有新消息"提示
 * 3. 用户点击按钮或手动滚到底 → 恢复自动滚动
 * 4. 对话切换 → 重置所有状态，立即滚到底部
 */

import { useState, useCallback, useRef, type RefObject } from 'react';

/** 滚动阈值配置 */
const SCROLL_THRESHOLDS = {
  /** 判断是否在底部的阈值（px）*/
  AT_BOTTOM: 100,
  /** 判断用户是否滚走的阈值（px）*/
  SCROLLED_AWAY: 200,
  /** 显示滚动按钮的阈值（px）*/
  SHOW_BUTTON: 300,
};

interface UseScrollManagerOptions {
  containerRef: RefObject<HTMLDivElement | null>;
  messagesEndRef: RefObject<HTMLDivElement | null>;
}

export function useScrollManager({ containerRef, messagesEndRef }: UseScrollManagerOptions) {
  // 滚动按钮显示状态
  const [showScrollButton, setShowScrollButton] = useState(false);
  // 用户是否主动滚走（打断自动滚动）
  const [userScrolledAway, setUserScrolledAway] = useState(false);
  // 是否有新消息（用户滚走时收到新内容）
  const [hasNewMessages, setHasNewMessages] = useState(false);

  // 防抖标记：避免程序触发的滚动被误判为用户操作
  const isProgrammaticScrollRef = useRef(false);
  // 上一次滚动位置（用于判断滚动方向）
  const lastScrollTopRef = useRef(0);

  /**
   * 强制滚动到底部（用户主动操作或对话切换）
   * - 重置所有状态
   * - 使用 smooth 或 instant 滚动
   */
  const forceScrollToBottom = useCallback((smooth = true) => {
    isProgrammaticScrollRef.current = true;

    // 重置状态
    setUserScrolledAway(false);
    setHasNewMessages(false);

    if (messagesEndRef.current) {
      messagesEndRef.current.scrollIntoView({
        behavior: smooth ? 'smooth' : 'auto',
        block: 'end',
      });
    }

    // 延迟重置标记，确保 scroll 事件处理完毕
    setTimeout(() => {
      isProgrammaticScrollRef.current = false;
    }, 100);
  }, [messagesEndRef]);

  /**
   * 智能滚动（新消息到达时调用）
   * - 如果用户未滚走 → 自动滚动到底部
   * - 如果用户已滚走 → 标记有新消息，不打断用户
   */
  const autoScrollIfNeeded = useCallback((smooth = true) => {
    if (userScrolledAway) {
      // 用户已滚走，标记有新消息
      setHasNewMessages(true);
    } else {
      // 用户在底部附近，自动滚动
      isProgrammaticScrollRef.current = true;

      if (messagesEndRef.current) {
        messagesEndRef.current.scrollIntoView({
          behavior: smooth ? 'smooth' : 'auto',
          block: 'end',
        });
      }

      setTimeout(() => {
        isProgrammaticScrollRef.current = false;
      }, 100);
    }
  }, [userScrolledAway, messagesEndRef]);

  /**
   * 滚动到底部（兼容旧 API）
   * - smooth=true: 平滑滚动（新消息、用户点击）
   * - smooth=false: 瞬时定位（对话切换），直接设置 scrollTop 无任何动画
   */
  const scrollToBottom = useCallback((smooth = true) => {
    isProgrammaticScrollRef.current = true;
    const container = containerRef.current;

    if (smooth) {
      // 平滑滚动：使用 scrollIntoView
      messagesEndRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' });
    } else if (container) {
      // 瞬时定位：直接设置 scrollTop（无任何动画，参考 ChatGPT）
      container.scrollTop = container.scrollHeight;
    }

    setTimeout(() => {
      isProgrammaticScrollRef.current = false;
    }, smooth ? 100 : 50);
  }, [containerRef, messagesEndRef]);

  /**
   * 处理滚动事件
   * - 检测用户滚动方向和位置
   * - 更新 userScrolledAway 和 showScrollButton 状态
   */
  const handleScroll = useCallback(() => {
    const container = containerRef.current;
    if (!container) return;

    // 如果是程序触发的滚动，跳过状态更新
    if (isProgrammaticScrollRef.current) return;

    const { scrollTop, scrollHeight, clientHeight } = container;
    const distanceFromBottom = scrollHeight - scrollTop - clientHeight;
    const isAtBottom = distanceFromBottom < SCROLL_THRESHOLDS.AT_BOTTOM;
    const isScrollingUp = scrollTop < lastScrollTopRef.current;

    // 更新上一次滚动位置
    lastScrollTopRef.current = scrollTop;

    // 用户向上滚动且超过阈值 → 标记为滚走
    if (isScrollingUp && distanceFromBottom > SCROLL_THRESHOLDS.SCROLLED_AWAY) {
      setUserScrolledAway(true);
    }

    // 用户滚回底部 → 重置状态
    if (isAtBottom) {
      setUserScrolledAway(false);
      setHasNewMessages(false);
    }

    // 显示/隐藏滚动按钮
    setShowScrollButton(distanceFromBottom > SCROLL_THRESHOLDS.SHOW_BUTTON);
  }, [containerRef]);

  /**
   * 重置滚动状态（对话切换时调用）
   */
  const resetScrollState = useCallback(() => {
    setUserScrolledAway(false);
    setShowScrollButton(false);
    setHasNewMessages(false);
    lastScrollTopRef.current = 0;
    isProgrammaticScrollRef.current = false;
  }, []);

  /**
   * 标记有新消息（外部调用，如后台刷新发现新消息）
   */
  const markNewMessages = useCallback(() => {
    if (userScrolledAway) {
      setHasNewMessages(true);
    }
  }, [userScrolledAway]);

  /**
   * 清除新消息标记
   */
  const clearNewMessages = useCallback(() => {
    setHasNewMessages(false);
  }, []);

  return {
    // 状态
    showScrollButton,
    userScrolledAway,
    hasNewMessages,
    // 状态设置器（兼容旧 API）
    setUserScrolledAway,
    setHasNewMessages,
    // 滚动方法
    scrollToBottom,
    forceScrollToBottom,
    autoScrollIfNeeded,
    // 事件处理
    handleScroll,
    // 状态管理
    resetScrollState,
    markNewMessages,
    clearNewMessages,
  };
}
