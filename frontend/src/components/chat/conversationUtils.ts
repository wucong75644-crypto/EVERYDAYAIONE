/**
 * 对话列表工具函数和类型定义
 */

import type { ConversationListItem } from '../../services/conversation';

/** 乐观更新参数 */
export interface OptimisticUpdate {
  conversationId: string;
  lastMessage: string;
}

/** 标题乐观更新参数 */
export interface OptimisticTitleUpdate {
  id: string;
  title: string;
}

/** 新对话乐观更新参数 */
export interface OptimisticNewConversation {
  id: string;
  title: string;
}

/** localStorage 缓存键 */
export const CONVERSATIONS_CACHE_KEY = 'everydayai_conversations_cache';

/**
 * 格式化日期分组标题
 */
export function formatDateGroup(dateStr: string): string {
  const date = new Date(dateStr);
  const today = new Date();
  const yesterday = new Date(today);
  yesterday.setDate(yesterday.getDate() - 1);

  if (date.toDateString() === today.toDateString()) {
    return '今天';
  } else if (date.toDateString() === yesterday.toDateString()) {
    return '昨天';
  } else {
    return `${date.getMonth() + 1}月${date.getDate()}日`;
  }
}

/**
 * 按日期分组对话列表
 */
export function groupConversationsByDate(
  conversations: ConversationListItem[]
): Record<string, ConversationListItem[]> {
  const groups: Record<string, ConversationListItem[]> = {};

  conversations.forEach((conv) => {
    const group = formatDateGroup(conv.updated_at);
    if (!groups[group]) {
      groups[group] = [];
    }
    groups[group].push(conv);
  });

  return groups;
}
