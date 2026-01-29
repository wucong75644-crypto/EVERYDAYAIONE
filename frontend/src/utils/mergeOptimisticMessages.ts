/**
 * 合并持久化消息和乐观更新消息的工具函数
 *
 * 处理以下场景：
 * 1. 去重：已持久化的消息不重复显示
 * 2. temp- 用户消息：检查内容是否已被真实消息替换
 * 3. streaming- 消息：区分聊天流式、媒体任务占位符、已完成流式
 * 4. 按时间排序
 */

import type { Message } from '../services/message';

export interface RuntimeState {
  streamingMessageId: string | null;
  optimisticMessages: Message[];
}

/**
 * 合并持久化消息和乐观更新消息
 *
 * @param persistedMessages - 已持久化的消息列表
 * @param runtimeState - 运行时状态（包含乐观更新消息）
 * @returns 合并并排序后的消息列表
 */
export function mergeOptimisticMessages(
  persistedMessages: Message[],
  runtimeState: RuntimeState | undefined
): Message[] {
  // 无运行时状态或无乐观消息，直接返回持久化消息
  if (!runtimeState || runtimeState.optimisticMessages.length === 0) {
    return persistedMessages;
  }

  // 创建持久化消息的ID集合
  const persistedIds = new Set(persistedMessages.map((m) => m.id));

  // 创建持久化用户消息的内容集合（用于检测 temp- 消息是否已被替换）
  const persistedUserContents = new Set(
    persistedMessages.filter((m) => m.role === 'user').map((m) => m.content)
  );

  // 过滤出需要显示的乐观消息
  const newOptimisticMessages = runtimeState.optimisticMessages.filter((m) => {
    // 已存在于持久化消息中（通过ID），跳过
    if (persistedIds.has(m.id)) return false;

    // temp- 用户消息：检查内容是否已有对应的持久化消息
    if (m.id.startsWith('temp-') && m.role === 'user') {
      // 如果持久化消息中已有相同内容的用户消息，说明已被替换
      return !persistedUserContents.has(m.content);
    }

    // streaming- AI消息需要区分聊天流式和媒体任务占位符
    if (m.id.startsWith('streaming-')) {
      // 检查是否是当前正在进行的聊天流式消息
      if (m.id === runtimeState.streamingMessageId) {
        // 聊天流式消息：显示（正在生成中）
        return true;
      }

      // 检查是否是媒体任务占位符（图片/视频生成中）
      const isMediaPlaceholder =
        m.content.includes('图片生成中') ||
        m.content.includes('视频生成中') ||
        m.content.includes('正在生成图片') ||
        m.content.includes('正在生成视频');

      if (isMediaPlaceholder) {
        // 媒体任务占位符：始终显示（会被 replaceMediaPlaceholder 替换为真实消息）
        return true;
      }

      // 已完成的聊天流式消息：检查 persistedMessages 中是否已有相同内容的 AI 消息
      // 如果有，说明真实消息已到达，过滤掉流式消息；否则继续显示
      const hasMatchingPersistedMessage = persistedMessages.some(
        (pm) => pm.role === 'assistant' && pm.content === m.content
      );
      return !hasMatchingPersistedMessage;
    }

    // 其他消息：显示
    return true;
  });

  // 合并并按时间排序
  const combined = [...persistedMessages, ...newOptimisticMessages];
  combined.sort((a, b) => new Date(a.created_at).getTime() - new Date(b.created_at).getTime());

  return combined;
}
