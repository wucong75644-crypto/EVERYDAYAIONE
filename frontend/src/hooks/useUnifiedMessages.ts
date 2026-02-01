/**
 * 统一消息读取 Hook
 *
 * 封装消息合并逻辑，组件无需关心数据来源：
 * - 持久化消息来自 useChatStore.messageCache
 * - 临时消息来自 useConversationRuntimeStore.states
 *
 * 内部使用 mergeOptimisticMessages 进行合并和去重
 */

import { useMemo } from 'react';
import { useChatStore } from '../stores/useChatStore';
import { useConversationRuntimeStore } from '../stores/useConversationRuntimeStore';
import { mergeOptimisticMessages } from '../utils/mergeOptimisticMessages';
import type { Message } from '../services/message';

/**
 * 获取指定对话的统一消息列表
 *
 * @param conversationId - 对话 ID，null 时返回空数组
 * @returns 合并后的消息列表（持久化消息 + 临时消息，已去重和排序）
 *
 * @example
 * ```tsx
 * const messages = useUnifiedMessages(conversationId);
 * return messages.map(msg => <MessageItem key={msg.id} message={msg} />);
 * ```
 */
export function useUnifiedMessages(conversationId: string | null): Message[] {
  // 订阅持久化消息（来自 useChatStore）
  const cachedEntry = useChatStore((state) =>
    conversationId ? state.messageCache.get(conversationId) : undefined
  );

  // 订阅运行时状态（来自 useConversationRuntimeStore）
  const runtimeState = useConversationRuntimeStore((state) =>
    conversationId ? state.states.get(conversationId) : undefined
  );

  // 合并消息（内部使用 mergeOptimisticMessages）
  const messages = useMemo(() => {
    if (!conversationId) return [];

    // 从缓存获取持久化消息（已统一为 API Message 格式）
    const persistedMessages: Message[] = cachedEntry?.messages ?? [];

    // 调用 mergeOptimisticMessages 进行合并
    return mergeOptimisticMessages(persistedMessages, runtimeState);
  }, [conversationId, cachedEntry, runtimeState]);

  return messages;
}

/**
 * 获取当前对话的统一消息列表（便捷方法）
 *
 * 自动从 useChatStore 获取 currentConversationId
 */
export function useCurrentMessages(): Message[] {
  const currentConversationId = useChatStore((state) => state.currentConversationId);
  return useUnifiedMessages(currentConversationId);
}
