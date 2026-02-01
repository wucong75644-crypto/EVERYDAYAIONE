/**
 * 消息加载自定义Hook
 *
 * 封装消息加载逻辑，包括缓存、竞态处理、后台刷新等
 *
 * 重构说明（方案B）：
 * - 删除了 toStoreMessage 和 convertCacheToApiMessages 格式转换函数
 * - 缓存直接存储 API Message 格式，无需转换
 * - 使用统一方法 setMessagesForConversation 写入缓存
 */

import { useState, useCallback, useRef } from 'react';
import axios from 'axios';
import { getMessages, type Message } from '../services/message';
import { useChatStore } from '../stores/useChatStore';

interface UseMessageLoaderOptions {
  conversationId: string | null;
  refreshTrigger?: number;
  /** 后台刷新发现新消息时的回调 */
  onNewMessages?: () => void;
}

export function useMessageLoader({ conversationId, refreshTrigger = 0, onNewMessages }: UseMessageLoaderOptions) {
  const [loading, setLoading] = useState(true); // 初始为 true，避免滚动逻辑提前触发
  const [hasMore, setHasMore] = useState(false);
  const lastRefreshTriggerRef = useRef(refreshTrigger);
  // 初始值为 null，确保首次渲染也会走"对话切换"分支
  const previousConversationIdRef = useRef<string | null>(null);

  const {
    getCachedMessages,
    setMessagesForConversation,
    isCacheExpired,
    touchCache,
  } = useChatStore();

  // 从后端加载消息
  const fetchMessages = useCallback(
    async (silent = false, signal?: AbortSignal): Promise<Message[] | null> => {
      if (!conversationId) return null;

      try {
        if (!silent) setLoading(true);
        const response = await getMessages(conversationId, 100, 0, undefined, signal);

        if (signal?.aborted) {
          return null;
        }

        return response.messages;
      } catch (error) {
        if (error instanceof Error && error.name === 'AbortError') {
          return null;
        }
        if (axios.isCancel(error)) {
          return null;
        }
        console.error('加载消息失败:', error);
        return null;
      } finally {
        if (!silent) setLoading(false);
      }
    },
    [conversationId]
  );

  // 加载对话消息（带缓存逻辑）
  const loadMessages = useCallback(
    async (signal?: AbortSignal) => {
      if (!conversationId) {
        previousConversationIdRef.current = null;
        return;
      }

      // 检测对话切换
      if (previousConversationIdRef.current !== conversationId) {
        previousConversationIdRef.current = conversationId;

        const cachedData = getCachedMessages(conversationId);
        if (cachedData && cachedData.messages && cachedData.messages.length > 0) {
          // 更新LRU访问顺序
          touchCache(conversationId);

          // 缓存已是 Message 格式，无需转换
          setLoading(false);
          setHasMore(cachedData.hasMore);
          // 有缓存时提前返回，避免下面的逻辑覆盖
          return;
        }

        // 无缓存时：显示加载中
        setLoading(true);
        // 继续执行下面的从后端加载逻辑
      }

      const currentConversationId = conversationId;
      const isRefreshTriggered = refreshTrigger !== lastRefreshTriggerRef.current;
      lastRefreshTriggerRef.current = refreshTrigger;

      const cached = getCachedMessages(conversationId);
      const cacheExpired = isCacheExpired(conversationId);

      if (cached && cached.messages && !isRefreshTriggered) {
        // 更新LRU访问顺序
        touchCache(conversationId);

        setHasMore(cached.hasMore);
        setLoading(false);

        if (cacheExpired) {
          const freshMessages = await fetchMessages(true, signal);

          if (signal?.aborted || currentConversationId !== conversationId) {
            return;
          }

          if (freshMessages && freshMessages.length > 0) {
            // 直接存储 API 返回的 Message 格式，无需转换
            setMessagesForConversation(conversationId, freshMessages, freshMessages.length >= 1000);

            // 通知有新消息（由 useScrollManager 处理显示逻辑）
            if (cached.messages && freshMessages.length > cached.messages.length) {
              onNewMessages?.();
              // 标记对话有新消息（用于切换对话时决定滚动行为）
              useChatStore.getState().markConversationUnread(conversationId);
            }
          }
        }
      } else {
        setLoading(true);
        const freshMessages = await fetchMessages(false, signal);

        if (signal?.aborted || currentConversationId !== conversationId) {
          setLoading(false);
          return;
        }

        if (freshMessages) {
          setHasMore(freshMessages.length >= 1000);
          // 直接存储 API 返回的 Message 格式，无需转换
          setMessagesForConversation(conversationId, freshMessages, freshMessages.length >= 1000);
        }
        setLoading(false);
      }
    },
    [conversationId, refreshTrigger, getCachedMessages, touchCache, isCacheExpired, setMessagesForConversation, fetchMessages, onNewMessages]
  );

  return {
    loading,
    hasMore,
    loadMessages,
  };
}
