/**
 * 统一消息读取 Hook
 *
 * 合并 messages 和 optimisticMessages，实时显示流式消息和乐观更新
 */

import { useMemo } from 'react';
import { useMessageStore, type Message } from '../stores/useMessageStore';

/** 空数组常量，避免每次返回新引用 */
const EMPTY_MESSAGES: Message[] = [];

/**
 * 合并消息列表和乐观消息
 *
 * 规则：
 * 1. 先取 messages（已持久化）
 * 2. 再追加 optimisticMessages 中不重复的（streaming/pending）
 * 3. 按 created_at 排序
 */
function mergeMessages(
  messages: Message[] | undefined,
  optimisticMessages: Message[] | undefined
): Message[] {
  const persisted = messages || [];
  const optimistic = optimisticMessages || [];

  if (optimistic.length === 0) {
    return persisted;
  }

  // 收集已持久化消息的 ID
  const persistedIds = new Set(persisted.map((m) => m.id));

  // 过滤出不重复的乐观消息
  // 注意：streaming-{id} 格式的消息需要特殊处理
  const newOptimistic = optimistic.filter((m) => {
    // 如果 ID 已存在于持久化消息中，跳过
    if (persistedIds.has(m.id)) return false;

    // 如果是 streaming- 前缀，检查对应的真实 ID 是否已存在
    if (m.id.startsWith('streaming-')) {
      const realId = m.id.replace('streaming-', '');
      if (persistedIds.has(realId)) return false;
    }

    return true;
  });

  if (newOptimistic.length === 0) {
    return persisted;
  }

  // 合并并按时间排序
  const merged = [...persisted, ...newOptimistic];
  merged.sort((a, b) => new Date(a.created_at).getTime() - new Date(b.created_at).getTime());

  return merged;
}

/**
 * 获取指定对话的消息列表
 *
 * @param conversationId - 对话 ID，null 时返回空数组
 * @returns 消息列表（包含乐观消息和流式消息）
 */
export function useUnifiedMessages(conversationId: string | null): Message[] {
  // 选择 messages 和 optimisticMessages
  const messages = useMessageStore((state) =>
    conversationId ? state.messages.get(conversationId) : undefined
  );

  const optimisticMessages = useMessageStore((state) =>
    conversationId ? state.optimisticMessages.get(conversationId) : undefined
  );

  // 使用 useMemo 缓存合并结果
  return useMemo(() => {
    if (!conversationId) return EMPTY_MESSAGES;
    return mergeMessages(messages, optimisticMessages);
  }, [conversationId, messages, optimisticMessages]);
}

/**
 * 获取当前对话的消息列表
 */
export function useCurrentMessages(): Message[] {
  const currentConversationId = useMessageStore((state) => state.currentConversationId);
  return useUnifiedMessages(currentConversationId);
}
