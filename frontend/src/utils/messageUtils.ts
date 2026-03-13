/**
 * 消息工具函数
 *
 * 从 useMessageStore 提取的辅助函数。
 */

import type {
  Message,
  MessageStatus,
  ContentPart,
  ImagePart,
  VideoPart,
} from '../types/message';

// ============================================================
// 内容提取函数
// ============================================================

/** 从 Message 提取文本内容 */
export function getTextContent(message: Message): string {
  // 兼容旧格式（content 可能是字符串而非 ContentPart[]）
  const rawContent = (message as unknown as { content: unknown }).content;
  if (typeof rawContent === 'string') {
    return rawContent;
  }

  if (!Array.isArray(message.content)) return '';

  for (const part of message.content) {
    if (part.type === 'text') {
      return part.text;
    }
  }
  return '';
}

/** 从 Message 提取图片 URL */
export function getImageUrls(message: Message): string[] {
  if (!Array.isArray(message.content)) return [];

  return message.content
    .filter((p): p is ImagePart & { url: string } => p.type === 'image' && !!p.url)
    .map((p) => p.url);
}

/** 从 Message 提取视频 URL */
export function getVideoUrls(message: Message): string[] {
  if (!Array.isArray(message.content)) return [];

  return message.content
    .filter((p): p is VideoPart => p.type === 'video')
    .map((p) => p.url);
}

// ============================================================
// 消息转换函数
// ============================================================

/** API 返回的原始消息（content 可能是字符串或数组） */
export interface RawApiMessage {
  id: string;
  conversation_id: string;
  role: string;
  content: string | ContentPart[];
  status?: string;
  is_error?: boolean;
  [key: string]: unknown;
}

/** 推断 MessageStatus：优先使用已有值，否则根据 is_error 推断 */
function resolveStatus(status?: string, isError?: boolean): MessageStatus {
  if (status === 'pending' || status === 'streaming' || status === 'completed' || status === 'failed') {
    return status;
  }
  return isError ? 'failed' : 'completed';
}

/** 转换旧格式消息为新格式（兼容 Message 和 API 原始数据） */
export function normalizeMessage(msg: RawApiMessage | Message): Message {
  const status = resolveStatus(msg.status as string | undefined, msg.is_error);

  // 如果 content 已经是数组，直接返回
  if (Array.isArray(msg.content)) {
    return { ...msg, status } as Message;
  }

  // 检查是否为 JSON 字符串数组（后端保存为 JSONB 但返回为字符串的情况）
  if (typeof msg.content === 'string' && msg.content.startsWith('[')) {
    try {
      const parsed = JSON.parse(msg.content);
      if (Array.isArray(parsed)) {
        return { ...msg, content: parsed, status } as Message;
      }
    } catch {
      // 不是有效 JSON，继续正常处理
    }
  }

  // 转换旧格式（纯文本 content）
  const content: ContentPart[] = [];

  if (typeof msg.content === 'string' && msg.content) {
    content.push({ type: 'text', text: msg.content });
  }

  return { ...msg, content, status } as Message;
}
