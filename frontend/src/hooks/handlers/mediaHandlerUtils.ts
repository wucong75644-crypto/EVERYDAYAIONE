/**
 * 消息处理器共享工具
 * 包含类型定义、错误处理、URL提取等工具函数
 */

import axios from 'axios';
import { createMessage, type Message, type GenerationParams } from '../../services/message';
import { createErrorMessage } from '../../utils/messageFactory';

/** 媒体生成配置 */
export interface MediaGenConfig {
  type: 'image' | 'video';
  conversationId: string;
  conversationTitle: string;
  placeholderText?: string;
  successContent: string;
  errorPrefix: string;
  pollInterval: number;
  creditsConsumed: number;
  userMessageTimestamp: string;
  preCreatedPlaceholderId?: string;
  placeholderTimestamp: string;
  generationParams?: GenerationParams;
  pollFn: (taskId: string) => Promise<{
    status: string;
    fail_msg?: string | null;
    image_urls?: string[];
    video_url?: string | null
  }>;
  extractMediaUrl: (result: unknown) => { image_url?: string; video_url?: string };
}

/** 媒体生成响应 */
export interface MediaResponse {
  status: string;
  task_id: string;
  credits_consumed: number;
  image_urls?: string[];
  video_url?: string | null;
}

/** 从错误中提取友好消息 */
export function extractErrorMessage(error: unknown): string {
  if (axios.isAxiosError(error)) {
    const responseData = error.response?.data;
    const backendMessage =
      responseData?.error?.message || responseData?.message || responseData?.detail;
    return backendMessage || error.message;
  }
  return error instanceof Error ? error.message : '未知错误';
}

/** 安全提取图片URL（带运行时校验） */
export function extractImageUrl(result: unknown): string | undefined {
  if (
    result &&
    typeof result === 'object' &&
    'image_urls' in result &&
    Array.isArray((result as { image_urls: unknown }).image_urls) &&
    typeof (result as { image_urls: string[] }).image_urls[0] === 'string'
  ) {
    return (result as { image_urls: string[] }).image_urls[0];
  }
  return undefined;
}

/** 安全提取视频URL（带运行时校验） */
export function extractVideoUrl(result: unknown): string | undefined {
  if (
    result &&
    typeof result === 'object' &&
    'video_url' in result &&
    typeof (result as { video_url: unknown }).video_url === 'string'
  ) {
    return (result as { video_url: string }).video_url;
  }
  return undefined;
}

/** 处理生成错误 */
export async function handleGenerationError(
  conversationId: string,
  errorPrefix: string,
  error: unknown,
  createdAt?: string,
  generationParams?: GenerationParams
): Promise<Message> {
  const errorMsg = extractErrorMessage(error);
  try {
    return await createMessage(conversationId, {
      content: `${errorPrefix}: ${errorMsg}`,
      role: 'assistant',
      is_error: true,
      created_at: createdAt,
      generation_params: generationParams,
    });
  } catch {
    return createErrorMessage(conversationId, errorPrefix, error, createdAt);
  }
}
