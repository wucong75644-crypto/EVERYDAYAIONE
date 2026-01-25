/**
 * 消息相关 API 服务
 */

import { request } from './api';
import type { DeleteMessageResponse } from '../types/message';

/** 消息类型（API 响应格式） */
export interface Message {
  id: string;
  conversation_id?: string;
  role: 'user' | 'assistant' | 'system';
  content: string;
  image_url?: string | null;
  video_url?: string | null;
  is_error?: boolean;
  credits_cost?: number;
  created_at: string;
}

/** 消息列表响应 */
export interface MessageListResponse {
  messages: Message[];
  total: number;
  has_more: boolean;
}

/** 流式回调接口（用于 regenerate） */
export interface StreamCallbacks {
  onStart?: () => void;
  onContent?: (text: string) => void;
  onDone?: (message: Message) => void;
  onError?: (error: string) => void;
}

/** 发送消息流式回调接口 */
export interface SendMessageStreamCallbacks {
  onUserMessage?: (message: Message) => void;
  onStart?: (model: string) => void;
  onContent?: (text: string) => void;
  onDone?: (assistantMessage: Message | null, creditsConsumed: number) => void;
  onError?: (error: string) => void;
}

/** 创建消息请求 */
export interface CreateMessageRequest {
  content: string;
  role: 'user' | 'assistant';
  image_url?: string | null;
  video_url?: string | null;
  credits_cost?: number;
}

/** 发送消息请求 */
export interface SendMessageStreamRequest {
  content: string;
  model_id?: string;
  image_url?: string | null;
  video_url?: string | null;
  thinking_effort?: 'minimal' | 'low' | 'medium' | 'high';
  thinking_mode?: 'default' | 'deep_think';
}

/**
 * 创建消息
 * @param conversationId 对话ID
 * @param data 消息数据
 */
export async function createMessage(
  conversationId: string,
  data: CreateMessageRequest
): Promise<Message> {
  return request<Message>({
    url: `/conversations/${conversationId}/messages/create`,
    method: 'POST',
    data,
  });
}

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
 * 流式发送消息
 * @param conversationId 对话ID
 * @param data 消息数据
 * @param callbacks 流式回调
 */
export async function sendMessageStream(
  conversationId: string,
  data: SendMessageStreamRequest,
  callbacks: SendMessageStreamCallbacks
): Promise<void> {
  const token = localStorage.getItem('access_token');
  const url = `/api/conversations/${conversationId}/messages/stream`;

  try {
    const response = await fetch(url, {
      method: 'POST',
      headers: {
        'Authorization': `Bearer ${token}`,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(data),
    });

    if (!response.ok) {
      const error = await response.json();
      throw new Error(error.message || '发送消息失败');
    }

    if (!response.body) {
      throw new Error('响应体为空');
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';

    while (true) {
      const { done, value } = await reader.read();

      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split('\n');
      buffer = lines.pop() || '';

      for (const line of lines) {
        if (!line.trim() || !line.startsWith('data: ')) continue;

        const data = line.slice(6); // 移除 "data: "
        if (data === '[DONE]') {
          break;
        }

        try {
          const event = JSON.parse(data);

          switch (event.type) {
            case 'user_message':
              callbacks.onUserMessage?.(event.data.user_message);
              break;
            case 'start':
              callbacks.onStart?.(event.data.model);
              break;
            case 'content':
              callbacks.onContent?.(event.data.text);
              break;
            case 'done':
              callbacks.onDone?.(
                event.data.assistant_message,
                event.data.credits_consumed
              );
              break;
            case 'error':
              callbacks.onError?.(event.data.message);
              break;
          }
        } catch (e) {
          console.error('解析 SSE 事件失败:', e);
        }
      }
    }
  } catch (error) {
    const message = error instanceof Error ? error.message : '未知错误';
    callbacks.onError?.(message);
    throw error;
  }
}

/**
 * 重新生成失败的消息（流式）
 * @param conversationId 对话ID
 * @param messageId 消息ID
 * @param callbacks 流式回调
 */
export async function regenerateMessageStream(
  conversationId: string,
  messageId: string,
  callbacks: StreamCallbacks
): Promise<void> {
  const token = localStorage.getItem('access_token');
  const url = `/api/conversations/${conversationId}/messages/${messageId}/regenerate`;

  try {
    // 添加超时控制
    const controller = new AbortController();
    const timeoutId = setTimeout(() => {
      controller.abort();
    }, 10000); // 10秒超时

    const response = await fetch(url, {
      method: 'POST',
      headers: {
        'Authorization': `Bearer ${token}`,
        'Content-Type': 'application/json',
      },
      signal: controller.signal,
    });

    clearTimeout(timeoutId);

    if (!response.ok) {
      const error = await response.json();
      throw new Error(error.message || '重新生成失败');
    }

    if (!response.body) {
      throw new Error('响应体为空');
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';

    while (true) {
      const { done, value } = await reader.read();

      if (done) {
        break;
      }

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split('\n');
      buffer = lines.pop() || '';

      for (const line of lines) {
        if (!line.trim() || !line.startsWith('data: ')) continue;

        const data = line.slice(6); // 移除 "data: "
        if (data === '[DONE]') {
          break;
        }

        try {
          const event = JSON.parse(data);

          switch (event.type) {
            case 'start':
              callbacks.onStart?.();
              break;
            case 'content':
              callbacks.onContent?.(event.data.text);
              break;
            case 'done':
              callbacks.onDone?.(event.data.assistant_message);
              break;
            case 'error':
              callbacks.onError?.(event.data.message);
              break;
          }
        } catch (e) {
          console.error('解析 SSE 事件失败:', e);
        }
      }
    }
  } catch (error) {
    // 特殊处理超时错误
    if (error instanceof Error && error.name === 'AbortError') {
      const message = '请求超时,后端服务可能未响应';
      callbacks.onError?.(message);
      throw new Error(message);
    }

    const message = error instanceof Error ? error.message : '未知错误';
    callbacks.onError?.(message);
    throw error;
  }
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
