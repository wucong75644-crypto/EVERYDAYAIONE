/**
 * 消息工厂函数
 *
 * 统一创建各类消息对象，避免重复代码
 */

import type { Message } from '../services/message';

/**
 * 唯一 ID 生成器
 * 使用时间戳 + 自增计数器 + 随机后缀，确保在快速连续调用时也能生成唯一 ID
 */
let idCounter = 0;
function generateUniqueId(prefix: string): string {
  const timestamp = Date.now();
  const counter = idCounter++;
  const random = Math.random().toString(36).slice(2, 7);
  return `${prefix}-${timestamp}-${counter}-${random}`;
}

/**
 * 获取当前时间戳（确保连续调用也能获得递增的时间戳）
 */
let lastTimestamp = 0;
function getIncrementalTimestamp(): number {
  const now = Date.now();
  // 确保时间戳严格递增
  lastTimestamp = now > lastTimestamp ? now : lastTimestamp + 1;
  return lastTimestamp;
}

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
    id: generateUniqueId('error'),
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
 * @param clientRequestId 客户端请求ID（用于乐观更新）
 */
export function createOptimisticUserMessage(
  content: string,
  conversationId: string,
  imageUrl: string | null = null,
  createdAt?: string,
  clientRequestId?: string
): Message {
  return {
    id: generateUniqueId('temp'),
    conversation_id: conversationId,
    role: 'user',
    content,
    image_url: imageUrl,
    video_url: null,
    credits_cost: 0,
    created_at: createdAt || new Date().toISOString(),
    client_request_id: clientRequestId,  // 客户端请求ID（用于后端匹配）
    status: 'pending',  // 初始状态为 pending
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
  const newStreamingId = generateUniqueId('streaming');
  const tempUserId = generateUniqueId('temp-user');

  // 使用递增时间戳确保用户消息在 AI 消息之前
  const userTs = getIncrementalTimestamp();
  const aiTs = getIncrementalTimestamp();

  const userTimestamp = new Date(userTs).toISOString();
  const aiTimestamp = new Date(aiTs).toISOString();

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
  /** 占位符时间戳（比用户消息晚，确保排序） */
  placeholderTimestamp: string;
  /** 临时占位符ID */
  tempPlaceholderId: string;
}

/**
 * 创建媒体生成所需的时间戳和ID
 *
 * 用于图片/视频生成时保持消息顺序：
 * - 用户消息在前
 * - AI占位符在后
 */
export function createMediaTimestamps(): MediaTimestamps {
  // 使用递增时间戳确保顺序正确
  const userTs = getIncrementalTimestamp();
  const placeholderTs = getIncrementalTimestamp();

  const userTimestamp = new Date(userTs).toISOString();
  const placeholderTimestamp = new Date(placeholderTs).toISOString();
  const tempPlaceholderId = generateUniqueId('streaming-temp');

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
