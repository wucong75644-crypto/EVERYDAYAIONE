/**
 * Virtua 滚动管理统一入口
 *
 * 以 virtua 为核心的滚动管理系统，提供：
 * - 智能自动滚动（新消息、流式内容）
 * - 用户滚动状态检测
 * - 滚动按钮显示控制
 * - 向上滚动加载更多历史消息
 *
 * 相比 Virtuoso 的改进：
 * - 使用 shift={true} 自动维护底部滚动位置
 * - 更小的包体积（~3KB）
 * - 更好的动态高度支持
 *
 * 重构记录：
 * - 2026-02-03：从 Virtuoso 迁移到 Virtua
 * - 2026-02-03：新增懒加载支持（向上滚动加载历史消息）
 */

import { useState, useCallback, useRef, useLayoutEffect, useEffect } from 'react';
import { type VListHandle } from 'virtua';
import { useChatStore } from '../stores/useChatStore';
import type { Message } from '../services/message';

interface UseVirtuaScrollOptions {
  /** 当前对话 ID */
  conversationId: string | null;
  /** 消息列表 */
  messages: Message[];
  /** 是否正在加载 */
  loading: boolean;
  /** 是否正在流式生成（外部传入） */
  isStreaming?: boolean;
  /** 是否还有更多历史消息 */
  hasMore?: boolean;
  /** 是否正在加载更多 */
  loadingMore?: boolean;
  /** 加载更多回调 */
  onLoadMore?: () => void;
}

interface UseVirtuaScrollReturn {
  // ========== VList Ref ==========
  /** VList 实例引用（传给 VList ref） */
  vlistRef: React.RefObject<VListHandle | null>;

  // ========== 状态 ==========
  /** 用户是否主动滚走 */
  userScrolledAway: boolean;
  /** 是否有新消息 */
  hasNewMessages: boolean;
  /** 是否显示滚动按钮 */
  showScrollButton: boolean;

  // ========== VList 回调（直接传给 VList） ==========
  /** 滚动事件回调（传给 VList onScroll） */
  handleScroll: (offset: number) => void;

  // ========== 辅助方法 ==========
  /** 滚动到底部 */
  scrollToBottom: (smooth?: boolean) => void;
  /** 设置用户滚走状态 */
  setUserScrolledAway: (value: boolean) => void;
  /** 设置新消息状态 */
  setHasNewMessages: (value: boolean) => void;
}

/** 判断是否在底部的阈值（像素） */
const AT_BOTTOM_THRESHOLD = 100;
/** 触发加载更多的顶部阈值（像素） */
const LOAD_MORE_THRESHOLD = 200;

export function useVirtuaScroll({
  conversationId,
  messages,
  loading,
  isStreaming = false,
  hasMore = false,
  loadingMore = false,
  onLoadMore,
}: UseVirtuaScrollOptions): UseVirtuaScrollReturn {
  // ========== VList Ref ==========
  const vlistRef = useRef<VListHandle | null>(null);

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

  // ========== 判断是否在底部 ==========
  const isAtBottom = useCallback((): boolean => {
    const handle = vlistRef.current;
    if (!handle) return true;

    const { scrollOffset, scrollSize, viewportSize } = handle;
    // scrollSize 是整个滚动内容的高度
    // scrollOffset 是当前滚动位置
    // viewportSize 是可视区域高度
    // 当 scrollOffset + viewportSize >= scrollSize - threshold 时，认为在底部
    return scrollOffset + viewportSize >= scrollSize - AT_BOTTOM_THRESHOLD;
  }, []);

  // ========== 判断是否在顶部（用于触发加载更多） ==========
  const isAtTop = useCallback((): boolean => {
    const handle = vlistRef.current;
    if (!handle) return false;

    const { scrollOffset } = handle;
    return scrollOffset < LOAD_MORE_THRESHOLD;
  }, []);

  // ========== 滚动到底部 ==========
  const scrollToBottom = useCallback((smooth = true) => {
    const handle = vlistRef.current;
    if (!handle || messages.length === 0) return;

    handle.scrollToIndex(messages.length - 1, {
      align: 'end',
      smooth,
    });
  }, [messages.length]);

  // ========== 重置状态 ==========
  const resetScrollState = useCallback(() => {
    setUserScrolledAway(false);
    setHasNewMessages(false);
    setShowScrollButton(false);
  }, []);

  // ========== 对话切换处理 ==========
  useLayoutEffect(() => {
    const prevId = prevConversationIdRef.current;

    if (conversationId !== prevId) {
      resetScrollState();
      hasInitialScrollRef.current = false;
      prevMessageCountRef.current = 0;
      prevConversationIdRef.current = conversationId;
    }
  }, [conversationId, resetScrollState]);

  // ========== 消息加载后初始滚动 ==========
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

  // ========== 新消息自动滚动 ==========
  useEffect(() => {
    const currentCount = messages.length;
    const prevCount = prevMessageCountRef.current;
    const isNewMessage = currentCount > prevCount;

    prevMessageCountRef.current = currentCount;

    // 初始滚动未完成时不自动滚动
    if (!hasInitialScrollRef.current) {
      return;
    }

    // 流式生成中禁用自动滚动（避免打断阅读）
    if (isStreaming) {
      return;
    }

    // 没有新消息时不滚动
    if (!isNewMessage) {
      return;
    }

    // 用户发送消息时（新增 user/temp- 消息），只重置滚走状态
    // 注意：不手动滚动，让 Virtua shift 模式自动维护底部位置，避免闪动
    const newMessages = messages.slice(prevCount);
    const hasUserMessage = newMessages.some(
      (m) => m.role === 'user' || m.id.startsWith('temp-')
    );
    if (hasUserMessage) {
      setUserScrolledAway(false);
      // shift 模式会自动维护底部位置，无需手动滚动
      return;
    }

    // 用户已滚走 → 不滚动，标记新消息
    if (userScrolledAway) {
      setHasNewMessages(true);
      return;
    }

    // 用户在底部且有新消息 → shift 模式会自动维护，无需手动滚动
    // 注释掉避免与 shift 模式冲突导致闪动
    // if (isAtBottom()) {
    //   scrollToBottom(true);
    // }
  }, [messages, userScrolledAway, isStreaming, scrollToBottom, isAtBottom]);

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

  // ========== VList onScroll 回调 ==========
  const handleScroll = useCallback((_offset: number) => {
    // 底部检测
    const atBottom = isAtBottom();
    setUserScrolledAway(!atBottom);
    if (atBottom) {
      setHasNewMessages(false);
    }
    setShowScrollButton(!atBottom);

    // 顶部检测：触发加载更多
    if (isAtTop() && hasMore && !loadingMore && onLoadMore) {
      onLoadMore();
    }
  }, [isAtBottom, isAtTop, hasMore, loadingMore, onLoadMore]);

  return {
    // VList Ref
    vlistRef,
    // 状态
    userScrolledAway,
    hasNewMessages,
    showScrollButton,
    // VList 回调
    handleScroll,
    // 辅助方法
    scrollToBottom,
    setUserScrolledAway,
    setHasNewMessages,
  };
}
