/**
 * 占位符常量和工具函数
 * 统一管理所有占位符相关的文字和逻辑
 *
 * 渲染决策优先级：
 *   大脑 _render.xxx > RENDER_CONFIG[type] > 硬编码兜底
 */

import { type Message, getTextContent } from '../stores/useMessageStore';
import type { RenderInstruction } from '../types/render';

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

// ============================================================
// 渲染配置表（Phase 1: 配置化，替代散落各处的硬编码）
// ============================================================

/** 单类型渲染配置 */
export interface RenderConfig {
  /** 生成中占位符文字 */
  loadingText: string;
  /** 完成后气泡文字（单个） */
  completedText: string;
  /** 完成后气泡文字（多个，{count} 为数量占位） */
  completedTextPlural?: string;
}

/** 各媒体类型的渲染配置 */
export const RENDER_CONFIG: Record<Exclude<MessageType, 'chat'>, RenderConfig> = {
  image: {
    loadingText: PLACEHOLDER_TEXT.IMAGE_GENERATING,
    completedText: '好的，来看看生成的图片',
    completedTextPlural: '好的，来看看生成的 {count} 张图片',
  },
  video: {
    loadingText: PLACEHOLDER_TEXT.VIDEO_GENERATING,
    completedText: '生成完成',
  },
  audio: {
    loadingText: PLACEHOLDER_TEXT.AUDIO_GENERATING,
    completedText: '生成完成',
  },
  '3d': {
    loadingText: PLACEHOLDER_TEXT.MODEL_3D_GENERATING,
    completedText: '生成完成',
  },
  code: {
    loadingText: PLACEHOLDER_TEXT.CODE_GENERATING,
    completedText: '生成完成',
  },
};

/** 获取完成后的气泡文字 */
export function getCompletedBubbleText(type: MessageType, count?: number): string {
  if (type === 'chat') return '';
  const config = RENDER_CONFIG[type];
  if (!config) return '';
  if (count && count > 1 && config.completedTextPlural) {
    return config.completedTextPlural.replace('{count}', String(count));
  }
  return config.completedText;
}

// ============================================================
// 占位符检测
// ============================================================

/** 占位符判断结果 */
export interface PlaceholderInfo {
  isPlaceholder: boolean;
  type?: MessageType;
  text?: string;
}

/**
 * 判断是否为占位符消息
 *
 * 优先使用 generation_params.type（大脑渲染指令），
 * 文字匹配作为旧消息的兼容兜底。
 */
export function getPlaceholderInfo(message: Message): PlaceholderInfo {
  const textContent = getTextContent(message);

  // Priority 1: generation_params.type（大脑的渲染指令）
  const genType = message.generation_params?.type;
  if (genType && genType !== 'chat' && message.status === 'pending') {
    const mediaType = genType as Exclude<MessageType, 'chat'>;
    const render = message.generation_params?._render as RenderInstruction | undefined;
    return {
      isPlaceholder: true,
      type: mediaType,
      text: render?.placeholder_text || textContent || MEDIA_PLACEHOLDER_MAP[mediaType] || '',
    };
  }

  // Priority 2: Chat streaming（空内容 + streaming 状态）
  if (!textContent && message.status === 'streaming') {
    return { isPlaceholder: true, type: 'chat', text: PLACEHOLDER_TEXT.CHAT_THINKING };
  }

  // Legacy fallback: 文字匹配（兼容旧消息，无 generation_params）
  if (textContent.includes(PLACEHOLDER_TEXT.IMAGE_GENERATING)) {
    return { isPlaceholder: true, type: 'image', text: textContent };
  }
  if (textContent.includes(PLACEHOLDER_TEXT.VIDEO_GENERATING)) {
    return { isPlaceholder: true, type: 'video', text: textContent };
  }
  if (textContent.includes(PLACEHOLDER_TEXT.AUDIO_GENERATING)) {
    return { isPlaceholder: true, type: 'audio', text: textContent };
  }

  return { isPlaceholder: false };
}

// ============================================================
// Agent Loop 步骤文字映射
// ============================================================

/** Agent Loop 工具名 → 前端展示文字 */
const AGENT_STEP_MAP: Record<string, string> = {
  web_search: '正在搜索',
  get_conversation_context: '正在查看对话',
  search_knowledge: '正在查阅知识库',
};

/** 根据工具名获取 Agent Loop 步骤展示文字 */
export function getAgentStepText(toolName: string): string {
  return AGENT_STEP_MAP[toolName] || 'AI 正在分析';
}

/**
 * 判断是否为媒体占位符（图片/视频/音频）
 */
export function isMediaPlaceholder(message: Message): boolean {
  const info = getPlaceholderInfo(message);
  return info.isPlaceholder && info.type !== 'chat';
}
