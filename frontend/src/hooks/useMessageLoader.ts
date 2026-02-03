/**
 * 消息加载自定义Hook
 *
 * 封装消息加载逻辑，包括缓存、竞态处理、后台刷新等
 *
 * 重构说明（方案B）：
 * - 删除了 toStoreMessage 和 convertCacheToApiMessages 格式转换函数
 * - 缓存直接存储 API Message 格式，无需转换
 * - 使用统一方法 setMessagesForConversation 写入缓存
 *
 * 懒加载支持：
 * - 首屏加载 30 条消息
 * - 支持向上滚动加载更多历史消息
 */

import { useState, useCallback, useRef } from 'react';
import axios from 'axios';
import { getMessages, type Message } from '../services/message';
import { useChatStore } from '../stores/useChatStore';

// ========== 配置常量 ==========
/** 首屏加载消息数量 */
const INITIAL_LOAD_LIMIT = 30;
/** 加载更多时每次加载的数量 */
const LOAD_MORE_LIMIT = 30;

interface UseMessageLoaderOptions {
  conversationId: string | null;
  refreshTrigger?: number;
}

export function useMessageLoader({ conversationId, refreshTrigger = 0 }: UseMessageLoaderOptions) {
  const [loading, setLoading] = useState(true); // 初始为 true，避免滚动逻辑提前触发
  const [hasMore, setHasMore] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const lastRefreshTriggerRef = useRef(refreshTrigger);
  // 初始值为 null，确保首次渲染也会走"对话切换"分支
  const previousConversationIdRef = useRef<string | null>(null);

  // 注意：不在组件级别订阅 store 方法，而是在回调内部通过 getState() 获取
  // 这样可以避免 store 状态变化导致 loadMessages 重建，进而触发 effect 反复执行

  // 从后端加载消息
  const fetchMessages = useCallback(
    async (silent = false, signal?: AbortSignal): Promise<Message[] | null> => {
      if (!conversationId) return null;

      try {
        if (!silent) setLoading(true);
        const response = await getMessages(conversationId, INITIAL_LOAD_LIMIT, 0, undefined, signal);

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
  // 通过 getState() 在回调内部获取 store 方法，避免依赖不稳定导致函数重建
  const loadMessages = useCallback(
    async (signal?: AbortSignal) => {
      if (!conversationId) {
        previousConversationIdRef.current = null;
        return;
      }

      // 在回调内部获取 store 方法（稳定，不会导致依赖变化）
      const store = useChatStore.getState();

      // 检测对话切换
      if (previousConversationIdRef.current !== conversationId) {
        previousConversationIdRef.current = conversationId;

        const cachedData = store.getCachedMessages(conversationId);
        if (cachedData && cachedData.messages && cachedData.messages.length > 0) {
          // 更新LRU访问顺序
          store.touchCache(conversationId);

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

      const cached = store.getCachedMessages(conversationId);
      const cacheExpired = store.isCacheExpired(conversationId);

      if (cached && cached.messages && !isRefreshTriggered) {
        // 更新LRU访问顺序
        store.touchCache(conversationId);

        setHasMore(cached.hasMore);
        setLoading(false);

        if (cacheExpired) {
          const freshMessages = await fetchMessages(true, signal);

          if (signal?.aborted || currentConversationId !== conversationId) {
            return;
          }

          if (freshMessages && freshMessages.length > 0) {
            // 直接存储 API 返回的 Message 格式，无需转换
            store.setMessagesForConversation(conversationId, freshMessages, freshMessages.length >= INITIAL_LOAD_LIMIT);

            // 标记对话有新消息（用于切换对话时决定滚动行为，由 useVirtuaScroll 处理滚动逻辑）
            if (cached.messages && freshMessages.length > cached.messages.length) {
              store.markConversationUnread(conversationId);
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
          setHasMore(freshMessages.length >= INITIAL_LOAD_LIMIT);
          // 直接存储 API 返回的 Message 格式，无需转换
          store.setMessagesForConversation(conversationId, freshMessages, freshMessages.length >= INITIAL_LOAD_LIMIT);
        }
        setLoading(false);
      }
    },
    [conversationId, refreshTrigger, fetchMessages]
  );

  // 加载更多历史消息（向上滚动时触发）
  const loadMore = useCallback(async () => {
    if (!conversationId || loadingMore) return;

    const store = useChatStore.getState();
    const cached = store.getCachedMessages(conversationId);

    // 没有缓存或没有更多消息时不加载
    if (!cached || !cached.hasMore) return;

    setLoadingMore(true);

    try {
      // 使用 offset 分页，从当前消息数量位置开始加载
      const offset = cached.messages.length;
      const response = await getMessages(conversationId, LOAD_MORE_LIMIT, offset);

      // 检查是否还有更多
      const newHasMore = response.messages.length >= LOAD_MORE_LIMIT;

      // 向缓存顶部追加消息
      store.prependMessages(conversationId, response.messages, newHasMore);
      setHasMore(newHasMore);
    } catch (error) {
      console.error('加载更多消息失败:', error);
    } finally {
      setLoadingMore(false);
    }
  }, [conversationId, loadingMore]);

  return {
    loading,
    hasMore,
    loadMessages,
    loadMore,
    loadingMore,
  };
}
