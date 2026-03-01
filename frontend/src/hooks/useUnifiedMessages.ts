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
 * 2. 再追加 optimisticMessages 中不重复的
 * 3. 按 created_at 排序（修复重新生成时的顺序问题）
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
  const newOptimistic = optimistic.filter((m) => !persistedIds.has(m.id));

  if (newOptimistic.length === 0) {
    return persisted;
  }

  // 合并后按 created_at 排序（修复重新生成时的顺序问题）
  const merged = [...persisted, ...newOptimistic];
  merged.sort((a, b) => {
    const timeDiff = new Date(a.created_at).getTime() - new Date(b.created_at).getTime();
    if (timeDiff !== 0) return timeDiff;
    // 时间戳相同时，用户消息排在 AI 消息之前（确保正确的对话顺序）
    if (a.role === 'user' && b.role === 'assistant') return -1;
    if (a.role === 'assistant' && b.role === 'user') return 1;
    return 0;
  });
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
    conversationId ? state.messages[conversationId] : undefined
  );

  const optimisticMessages = useMessageStore((state) =>
    conversationId ? state.optimisticMessages.get(conversationId) : undefined
  );

  // 使用 useMemo 缓存合并结果
  return useMemo(() => {
    if (!conversationId) return EMPTY_MESSAGES;

    const merged = mergeMessages(messages, optimisticMessages);

    return merged;
  }, [conversationId, messages, optimisticMessages]);
}

/**
 * 获取当前对话的消息列表
 */
export function useCurrentMessages(): Message[] {
  const currentConversationId = useMessageStore((state) => state.currentConversationId);
  return useUnifiedMessages(currentConversationId);
}
