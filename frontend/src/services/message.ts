/**
 * 消息相关 API 服务
 *
 * 注意：消息发送请使用 messageSender.ts 中的 sendMessage()
 * 本文件仅保留消息查询和删除 API
 */

import { request } from './api';
import type { DeleteMessageResponse } from '../types/message';
import type { RawApiMessage } from '../utils/messageUtils';
import type {
  AspectRatio,
  ImageResolution,
  ImageOutputFormat,
  VideoFrames,
  VideoAspectRatio,
} from '../constants/models';

// ============================================================
// 类型定义
// ============================================================

/** 图片生成参数 */
export interface ImageGenerationParams {
  aspectRatio: AspectRatio;
  resolution?: ImageResolution;  // 可选：部分模型不支持 resolution
  outputFormat: ImageOutputFormat;
  model: string;
}

/** 视频生成参数 */
export interface VideoGenerationParams {
  frames: VideoFrames;
  aspectRatio: VideoAspectRatio;
  removeWatermark: boolean;
  model: string;
}

/** 聊天生成参数 */
export interface ChatGenerationParams {
  model: string;
  thinkingEffort?: string;
  thinkingMode?: 'default' | 'deep_think';
}

/** 生成参数（用于重新生成时继承） */
export interface GenerationParams {
  image?: ImageGenerationParams;
  video?: VideoGenerationParams;
  chat?: ChatGenerationParams;
}

/** 消息状态（与后端保持一致） */
export type MessageStatus = 'pending' | 'streaming' | 'completed' | 'failed';

/** 消息列表响应（原始 API 格式，需要通过 normalizeMessage 转换） */
export interface MessageListResponse {
  messages: RawApiMessage[];  // 原始 API 数据，由 normalizeMessage 转换为 Message
  total: number;
  has_more: boolean;
}

/** 消息搜索响应 */
export interface MessageSearchResponse {
  messages: RawApiMessage[];
  total: number;
  query: string;  // 原始查询词，用于前端高亮
}

// ============================================================
// API 函数
// ============================================================

/**
 * 获取消息列表
 * @param conversationId 对话ID
 * @param limit 每页数量
 * @param offset 偏移量
 * @param beforeId 获取此消息之前的消息
 * @param signal AbortSignal for request cancellation
 */
export async function getMessages(
  conversationId: string,
  limit = 100,
  offset = 0,
  beforeId?: string,
  signal?: AbortSignal
): Promise<MessageListResponse> {
  return request<MessageListResponse>({
    url: `/conversations/${conversationId}/messages`,
    method: 'GET',
    params: { limit, offset, before_id: beforeId },
    signal,
  });
}

/**
 * 搜索对话内消息（关键词模糊匹配）
 *
 * 用于"翻不到的远期消息"场景，配合 SearchPanel UI 使用。
 * 后端用 PostgreSQL ILIKE 在 JSONB content 字段上做模糊匹配。
 *
 * @param conversationId 对话 ID
 * @param query 搜索关键词（≥1 字符，≤200 字符）
 * @param limit 返回上限（默认 20，硬上限 100）
 * @param signal AbortSignal 用于取消请求
 */
export async function searchMessages(
  conversationId: string,
  query: string,
  limit = 20,
  signal?: AbortSignal,
): Promise<MessageSearchResponse> {
  return request<MessageSearchResponse>({
    url: `/conversations/${conversationId}/messages/search`,
    method: 'GET',
    params: { q: query, limit },
    signal,
  });
}

/**
 * 删除消息
 * @param messageId 消息ID
 * @returns 删除结果
 */
export async function deleteMessage(messageId: string): Promise<DeleteMessageResponse> {
  return request<DeleteMessageResponse>({
    url: `/messages/${messageId}`,
    method: 'DELETE',
  });
}

/**
 * 通过消息 ID 取消关联的后台任务
 * 用于删除 streaming/pending 占位符时清理后端 tasks 表中的记录
 * @param messageId 占位符消息 ID（对应 tasks 表的 placeholder_message_id 或 assistant_message_id）
 */
export async function cancelTaskByMessageId(messageId: string): Promise<void> {
  await request({
    url: `/tasks/cancel-by-message/${messageId}`,
    method: 'POST',
  });
}
