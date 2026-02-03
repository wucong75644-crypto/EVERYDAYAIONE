/**
 * Virtuoso 滚动管理统一入口
 *
 * 以 react-virtuoso 为核心的滚动管理系统，提供：
 * - 智能自动滚动（新消息、流式内容）
 * - 用户滚动状态检测
 * - 滚动按钮显示控制
 *
 * 设计原则（参考大厂实践：ChatGPT、豆包等）：
 * 1. Virtuoso 优先：所有滚动操作通过 Virtuoso API 实现
 * 2. 统一入口：一个 hook 管理所有滚动状态和行为
 * 3. 流式友好：流式生成中禁用自动滚动，避免打断阅读
 * 4. 简单直接：切换对话直接滚动到底部，不保存滚动位置
 *
 * 重构记录：
 * - 2026-02-02：替换原有分散的滚动 hooks
 * - 2026-02-03：删除滚动位置记忆功能，简化逻辑
 */

import { useState, useCallback, useRef, useLayoutEffect, useEffect } from 'react';
import { type VirtuosoHandle } from 'react-virtuoso';
import { useChatStore } from '../stores/useChatStore';
import type { Message } from '../services/message';

interface UseVirtuosoScrollOptions {
  /** 当前对话 ID */
  conversationId: string | null;
  /** 消息列表 */
  messages: Message[];
  /** 是否正在加载 */
  loading: boolean;
  /** 是否正在流式生成（外部传入） */
  isStreaming?: boolean;
}

interface UseVirtuosoScrollReturn {
  // ========== Virtuoso Ref ==========
  /** Virtuoso 实例引用（传给 Virtuoso ref） */
  virtuosoRef: React.RefObject<VirtuosoHandle | null>;

  // ========== 状态 ==========
  /** 用户是否主动滚走 */
  userScrolledAway: boolean;
  /** 是否有新消息 */
  hasNewMessages: boolean;
  /** 是否显示滚动按钮 */
  showScrollButton: boolean;

  // ========== Virtuoso 回调（直接传给 Virtuoso） ==========
  /** 自动滚动决策（传给 Virtuoso followOutput） */
  followOutput: (isAtBottom: boolean) => 'smooth' | 'auto' | false;
  /** 底部状态变化回调（传给 Virtuoso atBottomStateChange） */
  atBottomStateChange: (atBottom: boolean) => void;
  /** 获取 scroller 引用（传给 Virtuoso scrollerRef，预留扩展） */
  scrollerRef: (ref: HTMLElement | Window | null) => void;

  // ========== 辅助方法 ==========
  /** 滚动到底部 */
  scrollToBottom: (smooth?: boolean) => void;
  /** 设置用户滚走状态 */
  setUserScrolledAway: (value: boolean) => void;
  /** 设置新消息状态 */
  setHasNewMessages: (value: boolean) => void;
}

export function useVirtuosoScroll({
  conversationId,
  messages,
  loading,
  isStreaming = false,
}: UseVirtuosoScrollOptions): UseVirtuosoScrollReturn {
  // ========== Virtuoso Ref ==========
  const virtuosoRef = useRef<VirtuosoHandle | null>(null);

  // ========== 状态 ==========
  const [userScrolledAway, setUserScrolledAway] = useState(false);
  const [hasNewMessages, setHasNewMessages] = useState(false);
  const [showScrollButton, setShowScrollButton] = useState(false);

  // ========== 内部 Refs ==========
  /** 上一次消息数量（用于区分新消息添加和流式更新） */
  const prevMessageCountRef = useRef(messages.length);
  /** 上一个对话 ID */
  const prevConversationIdRef = useRef<string | null>(null);
  /** 是否已完成初始滚动 */
  const hasInitialScrollRef = useRef(false);
  /** 上一次的 streaming 状态（用于检测流结束） */
  const prevIsStreamingRef = useRef(false);

  // ========== Store 方法 ==========
  const hasUnreadMessages = useChatStore((state) => state.hasUnreadMessages);
  const clearConversationUnread = useChatStore((state) => state.clearConversationUnread);

  // ========== scrollerRef 回调（传给 Virtuoso，预留扩展） ==========
  const scrollerRef = useCallback((_ref: HTMLElement | Window | null) => {
    // 预留接口，供将来扩展使用（如手动监听 scroll 事件、埋点等）
  }, []);

  // ========== 滚动到底部 ==========
  const scrollToBottom = useCallback((smooth = true) => {
    virtuosoRef.current?.scrollToIndex({
      index: 'LAST',
      behavior: smooth ? 'smooth' : 'auto',
      align: 'end',
    });
  }, []);

  // ========== 重置状态 ==========
  const resetScrollState = useCallback(() => {
    setUserScrolledAway(false);
    setHasNewMessages(false);
    setShowScrollButton(false);
  }, []);

  // ========== 对话切换处理 ==========
  // 简化逻辑：切换对话时重置状态，不保存滚动位置
  useLayoutEffect(() => {
    const prevId = prevConversationIdRef.current;

    if (conversationId !== prevId) {
      // 重置状态
      resetScrollState();
      hasInitialScrollRef.current = false;
      prevMessageCountRef.current = 0;
      prevConversationIdRef.current = conversationId;
    }
  }, [conversationId, resetScrollState]);

  // ========== 消息加载后初始滚动 ==========
  // 简化逻辑：直接滚动到底部
  useLayoutEffect(() => {
    if (!conversationId || messages.length === 0 || loading || hasInitialScrollRef.current) {
      return;
    }

    // 使用双重 RAF 确保 DOM 完全渲染
    requestAnimationFrame(() => {
      requestAnimationFrame(() => {
        if (hasInitialScrollRef.current) return;

        // 清除未读标记
        if (hasUnreadMessages(conversationId)) {
          clearConversationUnread(conversationId);
        }

        // 直接滚动到底部（瞬时）
        scrollToBottom(false);
        hasInitialScrollRef.current = true;
      });
    });
  }, [conversationId, messages.length, loading, scrollToBottom, hasUnreadMessages, clearConversationUnread]);

  // ========== 流式结束时自动滚动到底部 ==========
  useEffect(() => {
    // 检测流式结束（从 true 变为 false）
    if (prevIsStreamingRef.current && !isStreaming) {
      // 流结束，如果用户没有滚走，自动滚动到底部
      if (!userScrolledAway) {
        scrollToBottom(true);
      }
    }
    prevIsStreamingRef.current = isStreaming;
  }, [isStreaming, userScrolledAway, scrollToBottom]);

  // ========== Virtuoso followOutput 回调 ==========
  const followOutput = useCallback((isAtBottom: boolean): 'smooth' | 'auto' | false => {
    const currentCount = messages.length;
    const prevCount = prevMessageCountRef.current;
    const isNewMessage = currentCount > prevCount;

    // 更新计数
    prevMessageCountRef.current = currentCount;

    // 初始滚动未完成时不自动滚动
    if (!hasInitialScrollRef.current) {
      return false;
    }

    // 流式生成中禁用自动滚动（避免打断阅读）
    if (isStreaming) {
      return false;
    }

    // 用户发送消息时（新增 user/temp- 消息），重置滚走状态
    if (isNewMessage) {
      const newMessages = messages.slice(prevCount);
      const hasUserMessage = newMessages.some(
        (m) => m.role === 'user' || m.id.startsWith('temp-')
      );
      if (hasUserMessage) {
        setUserScrolledAway(false);
        return 'smooth';
      }
    }

    // 用户已滚走且有新消息 → 不滚动，标记新消息
    if (userScrolledAway && isNewMessage) {
      setHasNewMessages(true);
      return false;
    }

    // 在底部 → 平滑滚动
    if (isAtBottom) {
      return 'smooth';
    }

    // 用户未滚走 → 平滑滚动
    if (!userScrolledAway) {
      return 'smooth';
    }

    return false;
  }, [messages, userScrolledAway, isStreaming]);

  // ========== Virtuoso atBottomStateChange 回调 ==========
  const atBottomStateChange = useCallback((atBottom: boolean) => {
    setUserScrolledAway(!atBottom);
    if (atBottom) {
      setHasNewMessages(false);
    }
    setShowScrollButton(!atBottom);
  }, []);

  return {
    // Virtuoso Ref
    virtuosoRef,
    // 状态
    userScrolledAway,
    hasNewMessages,
    showScrollButton,
    // Virtuoso 回调
    followOutput,
    atBottomStateChange,
    scrollerRef,
    // 辅助方法
    scrollToBottom,
    setUserScrolledAway,
    setHasNewMessages,
  };
}
