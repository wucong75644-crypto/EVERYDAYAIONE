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
 * @param createdAt 可选时间戳（用于保持消息顺序）
 */
export function createErrorMessage(
  conversationId: string,
  errorText: string,
  error: unknown,
  createdAt?: string
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
    created_at: createdAt || new Date().toISOString(),
  };
}

/**
 * 创建乐观用户消息（立即显示，无需等待数据库）
 *
 * @param content 消息内容
 * @param conversationId 对话ID
 * @param imageUrl 图片URL（可选）
 * @param createdAt 可选的时间戳（用于保持与占位符的顺序一致）
 */
export function createOptimisticUserMessage(
  content: string,
  conversationId: string,
  imageUrl: string | null = null,
  createdAt?: string
): Message {
  return {
    id: `temp-${Date.now()}`,
    conversation_id: conversationId,
    role: 'user',
    content,
    image_url: imageUrl,
    video_url: null,
    credits_cost: 0,
    created_at: createdAt || new Date().toISOString(),
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

/**
 * 媒体生成时间戳数据
 */
export interface MediaTimestamps {
  /** 用户消息时间戳 */
  userTimestamp: string;
  /** 占位符时间戳（比用户消息晚1ms，确保排序） */
  placeholderTimestamp: string;
  /** 临时占位符ID */
  tempPlaceholderId: string;
}

/**
 * 创建媒体生成所需的时间戳和ID
 *
 * 用于图片/视频生成时保持消息顺序：
 * - 用户消息在前
 * - AI占位符在后（+1ms）
 */
export function createMediaTimestamps(): MediaTimestamps {
  const userTimestamp = new Date().toISOString();
  const placeholderTimestamp = new Date(
    new Date(userTimestamp).getTime() + 1
  ).toISOString();
  const tempPlaceholderId = `streaming-temp-${Date.now()}-${Math.random().toString(36).slice(2, 9)}`;

  return { userTimestamp, placeholderTimestamp, tempPlaceholderId };
}

/**
 * 创建媒体生成的乐观消息对（用户消息 + AI占位符）
 *
 * @param conversationId 对话ID
 * @param content 用户消息内容
 * @param imageUrl 用户上传的图片URL（可选）
 * @param loadingText 占位符显示文本
 * @param timestamps 预生成的时间戳数据
 */
export function createMediaOptimisticPair(
  conversationId: string,
  content: string,
  imageUrl: string | null,
  loadingText: string,
  timestamps: MediaTimestamps
): { userMessage: Message; placeholder: Message } {
  // 使用预生成的时间戳，确保用户消息时间 < 占位符时间，排序后顺序正确
  const userMessage = createOptimisticUserMessage(
    content,
    conversationId,
    imageUrl,
    timestamps.userTimestamp
  );
  const placeholder = createStreamingPlaceholder(
    conversationId,
    timestamps.tempPlaceholderId,
    loadingText,
    timestamps.placeholderTimestamp
  );

  return { userMessage, placeholder };
}
