/**
 * 消息加载自定义Hook
 *
 * 封装消息加载逻辑，包括缓存、竞态处理、后台刷新等
 */

import { useState, useCallback, useRef } from 'react';
import axios from 'axios';
import { getMessages, type Message } from '../services/message';
import { useChatStore, type Message as CacheMessage } from '../stores/useChatStore';

/** 将 API Message 转换为缓存 Message 格式 */
function toStoreMessage(msg: Message): CacheMessage {
  return {
    id: msg.id,
    role: msg.role === 'system' ? 'assistant' : msg.role,
    content: msg.content,
    imageUrl: msg.image_url ?? undefined,
    videoUrl: msg.video_url ?? undefined,
    createdAt: msg.created_at,
  };
}

/** 将缓存消息转换为 API Message 格式（过滤临时消息） */
function convertCacheToApiMessages(
  messages: CacheMessage[],
  conversationId: string
): Message[] {
  return messages
    .filter((m) => !m.id.startsWith('temp-') && !m.id.startsWith('streaming-') && !m.id.startsWith('error-'))
    .map((m) => ({
      id: m.id,
      conversation_id: conversationId,
      role: m.role,
      content: m.content,
      image_url: m.imageUrl ?? null,
      video_url: m.videoUrl ?? null,
      credits_cost: 0,
      created_at: m.createdAt,
    }));
}

interface UseMessageLoaderOptions {
  conversationId: string | null;
  refreshTrigger?: number;
  /** 后台刷新发现新消息时的回调 */
  onNewMessages?: () => void;
}

export function useMessageLoader({ conversationId, refreshTrigger = 0, onNewMessages }: UseMessageLoaderOptions) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [loading, setLoading] = useState(false);
  const [hasMore, setHasMore] = useState(false);
  const lastRefreshTriggerRef = useRef(refreshTrigger);
  const previousConversationIdRef = useRef<string | null>(conversationId);

  const {
    getCachedMessages,
    setCachedMessages,
    updateCachedMessages,
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
        setMessages([]);
        previousConversationIdRef.current = null;
        return;
      }

      // 检测对话切换
      if (previousConversationIdRef.current !== conversationId) {
        const cachedData = getCachedMessages(conversationId);
        if (cachedData && cachedData.messages && cachedData.messages.length > 0) {
          // 更新LRU访问顺序
          touchCache(conversationId);

          const cachedMessages = convertCacheToApiMessages(cachedData.messages, conversationId);

          requestAnimationFrame(() => {
            setMessages(cachedMessages);
            setLoading(false);
            setHasMore(cachedData.hasMore);
          });
        } else {
          setMessages([]);
          setLoading(true);
        }
        previousConversationIdRef.current = conversationId;
      }

      const currentConversationId = conversationId;
      const isRefreshTriggered = refreshTrigger !== lastRefreshTriggerRef.current;
      lastRefreshTriggerRef.current = refreshTrigger;

      const cached = getCachedMessages(conversationId);
      const cacheExpired = isCacheExpired(conversationId);

      if (cached && cached.messages && !isRefreshTriggered) {
        // 更新LRU访问顺序
        touchCache(conversationId);

        setMessages(convertCacheToApiMessages(cached.messages, conversationId));
        setHasMore(cached.hasMore);
        setLoading(false);

        if (cacheExpired) {
          const freshMessages = await fetchMessages(true, signal);

          if (signal?.aborted || currentConversationId !== conversationId) {
            return;
          }

          if (freshMessages && freshMessages.length > 0) {
            const newStoreMessages = freshMessages.map(toStoreMessage);
            updateCachedMessages(conversationId, newStoreMessages);

            // 通知有新消息（由 useScrollManager 处理显示逻辑）
            if (cached.messages && freshMessages.length > cached.messages.length) {
              onNewMessages?.();
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
          setMessages(freshMessages);
          setHasMore(freshMessages.length >= 1000);
          const storeMessages = freshMessages.map(toStoreMessage);
          setCachedMessages(conversationId, {
            messages: storeMessages,
            hasMore: freshMessages.length >= 1000,
          });
        }
        setLoading(false);
      }
    },
    [conversationId, refreshTrigger, getCachedMessages, touchCache, isCacheExpired, setCachedMessages, updateCachedMessages, fetchMessages, onNewMessages]
  );

  return {
    messages,
    setMessages,
    loading,
    hasMore,
    loadMessages,
    toStoreMessage,
    getCachedMessages,
    updateCachedMessages,
  };
}
