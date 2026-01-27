/**
 * 消息工厂函数
 *
 * 统一创建各类消息对象，避免重复代码
 */

import type { Message } from '../services/message';

/**
 * 创建错误消息
 *
 * @param conversationId 对话ID
 * @param errorText 错误前缀文本
 * @param error 错误对象
 */
export function createErrorMessage(
  conversationId: string,
  errorText: string,
  error: unknown
): Message {
  return {
    id: `error-${Date.now()}`,
    conversation_id: conversationId,
    role: 'assistant',
    content: `${errorText}: ${error instanceof Error ? error.message : '未知错误'}`,
    image_url: null,
    video_url: null,
    is_error: true,
    credits_cost: 0,
    created_at: new Date().toISOString(),
  };
}

/**
 * 创建乐观用户消息（立即显示，无需等待数据库）
 *
 * @param content 消息内容
 * @param conversationId 对话ID
 * @param imageUrl 图片URL（可选）
 */
export function createOptimisticUserMessage(
  content: string,
  conversationId: string,
  imageUrl: string | null = null
): Message {
  return {
    id: `temp-${Date.now()}`,
    conversation_id: conversationId,
    role: 'user',
    content,
    image_url: imageUrl,
    video_url: null,
    credits_cost: 0,
    created_at: new Date().toISOString(),
  };
}

/**
 * 创建临时消息对（用户消息 + AI 占位消息）
 *
 * @param conversationId 对话ID
 * @param userMessage 原用户消息
 * @param loadingText 加载中显示的文本
 */
export function createTempMessagePair(
  conversationId: string,
  userMessage: Message,
  loadingText: string
): {
  tempUserMessage: Message;
  tempAiMessage: Message;
  tempUserId: string;
  newStreamingId: string;
} {
  const newStreamingId = `streaming-${Date.now()}`;
  const tempUserId = `temp-user-${Date.now()}`;

  // 时间戳：确保用户消息在 AI 消息之前（+1ms 确保排序稳定）
  const userTimestamp = new Date().toISOString();
  const aiTimestamp = new Date(Date.now() + 1).toISOString();

  const tempUserMessage: Message = {
    id: tempUserId,
    conversation_id: conversationId,
    role: 'user',
    content: userMessage.content,
    image_url: userMessage.image_url,
    video_url: null,
    credits_cost: 0,
    created_at: userTimestamp,
  };

  const tempAiMessage: Message = {
    id: newStreamingId,
    conversation_id: conversationId,
    role: 'assistant',
    content: loadingText,
    image_url: null,
    video_url: null,
    credits_cost: 0,
    created_at: aiTimestamp,
  };

  return { tempUserMessage, tempAiMessage, tempUserId, newStreamingId };
}

/**
 * 创建流式占位符消息（用于图片/视频生成中状态）
 *
 * @param conversationId 对话ID
 * @param placeholderId 占位符消息ID（通常为 streaming-${taskId}）
 * @param loadingText 加载中显示的文本
 * @param createdAt 可选的时间戳（用于保持与用户消息的顺序）
 */
export function createStreamingPlaceholder(
  conversationId: string,
  placeholderId: string,
  loadingText: string,
  createdAt?: string
): Message {
  return {
    id: placeholderId,
    conversation_id: conversationId,
    role: 'assistant',
    content: loadingText,
    image_url: null,
    video_url: null,
    credits_cost: 0,
    created_at: createdAt || new Date().toISOString(),
  };
}
