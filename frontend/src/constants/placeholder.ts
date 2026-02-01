/**
 * 占位符常量和工具函数
 * 统一管理所有占位符相关的文字和逻辑
 */

import type { Message } from '../services/message';

/** 消息类型 */
export type MessageType = 'chat' | 'image' | 'video' | 'audio' | '3d' | 'code';

/** 占位符文字常量（统一管理） */
export const PLACEHOLDER_TEXT = {
  // 聊天占位符（首次生成和重新生成都用这个）
  CHAT_THINKING: 'AI 正在思考',

  // 媒体占位符（首次生成和重新生成都用这个）
  IMAGE_GENERATING: '图片生成中',
  VIDEO_GENERATING: '视频生成中',
  AUDIO_GENERATING: '音频生成中',
  MODEL_3D_GENERATING: '3D 模型生成中',
  CODE_GENERATING: '代码生成中',
} as const;

/** 媒体类型到占位符文字的映射 */
const MEDIA_PLACEHOLDER_MAP: Record<Exclude<MessageType, 'chat'>, string> = {
  image: PLACEHOLDER_TEXT.IMAGE_GENERATING,
  video: PLACEHOLDER_TEXT.VIDEO_GENERATING,
  audio: PLACEHOLDER_TEXT.AUDIO_GENERATING,
  '3d': PLACEHOLDER_TEXT.MODEL_3D_GENERATING,
  code: PLACEHOLDER_TEXT.CODE_GENERATING,
};

/**
 * 获取占位符文字（聊天/媒体通用）
 * @param type 消息类型
 */
export function getPlaceholderText(type: MessageType): string {
  if (type === 'chat') {
    return PLACEHOLDER_TEXT.CHAT_THINKING;
  }
  return MEDIA_PLACEHOLDER_MAP[type];
}

/** 占位符判断结果 */
export interface PlaceholderInfo {
  isPlaceholder: boolean;
  type?: MessageType;
  text?: string;
}

/**
 * 判断是否为占位符消息
 */
export function getPlaceholderInfo(message: Message): PlaceholderInfo {
  const content = message.content;

  // 检查图片占位符
  if (content.includes(PLACEHOLDER_TEXT.IMAGE_GENERATING)) {
    return { isPlaceholder: true, type: 'image', text: content };
  }

  // 检查视频占位符
  if (content.includes(PLACEHOLDER_TEXT.VIDEO_GENERATING)) {
    return { isPlaceholder: true, type: 'video', text: content };
  }

  // 检查音频占位符
  if (content.includes(PLACEHOLDER_TEXT.AUDIO_GENERATING)) {
    return { isPlaceholder: true, type: 'audio', text: content };
  }

  // 检查聊天占位符（空内容 + streaming ID）
  if (!content && message.id.startsWith('streaming-')) {
    return { isPlaceholder: true, type: 'chat', text: PLACEHOLDER_TEXT.CHAT_THINKING };
  }

  return { isPlaceholder: false };
}

/**
 * 判断是否为媒体占位符（图片/视频/音频）
 */
export function isMediaPlaceholder(message: Message): boolean {
  const info = getPlaceholderInfo(message);
  return info.isPlaceholder && info.type !== 'chat';
}
