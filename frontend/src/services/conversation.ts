/**
 * 对话 API 服务
 */

import { request } from './api';

/** 对话级设置（per-conversation 持久化） */
export interface ChatSettings {
  deep_think_mode?: boolean;
  thinking_effort?: string;
  temperature?: number;
  top_p?: number;
  top_k?: number;
  max_output_tokens?: number;
  image_aspect_ratio?: string;
  image_resolution?: string;
  image_output_format?: string;
  image_num_images?: number;
  video_frames?: number;
  video_aspect_ratio?: string;
  video_remove_watermark?: boolean;
}

export interface Conversation {
  id: string;
  title: string;
  model_id: string | null;
  chat_settings?: ChatSettings | null;
  message_count: number;
  credits_consumed: number;
  created_at: string;
  updated_at: string;
}

export interface ConversationListItem {
  id: string;
  title: string;
  last_message: string | null;
  model_id: string | null;
  chat_settings?: ChatSettings | null;
  updated_at: string;
  source?: 'web' | 'wecom';
}

export interface ConversationListResponse {
  conversations: ConversationListItem[];
  total: number;
}

export interface CreateConversationRequest {
  title?: string;
  model_id?: string;
  chat_settings?: ChatSettings;
}

export interface UpdateConversationRequest {
  title?: string;
  model_id?: string;
  chat_settings?: ChatSettings;
}

/**
 * 创建对话
 */
export async function createConversation(
  data: CreateConversationRequest = {}
): Promise<Conversation> {
  return request<Conversation>({
    method: 'POST',
    url: '/conversations',
    data,
  });
}

/**
 * 获取对话列表
 */
export async function getConversationList(
  limit = 50,
  offset = 0
): Promise<ConversationListResponse> {
  return request<ConversationListResponse>({
    method: 'GET',
    url: '/conversations',
    params: { limit, offset },
  });
}

/**
 * 获取对话详情
 */
export async function getConversation(id: string): Promise<Conversation> {
  return request<Conversation>({
    method: 'GET',
    url: `/conversations/${id}`,
  });
}

/**
 * 更新对话标题
 */
export async function updateConversation(
  id: string,
  data: UpdateConversationRequest
): Promise<Conversation> {
  return request<Conversation>({
    method: 'PUT',
    url: `/conversations/${id}`,
    data,
  });
}

/**
 * 删除对话
 */
export async function deleteConversation(id: string): Promise<void> {
  return request<void>({
    method: 'DELETE',
    url: `/conversations/${id}`,
  });
}
