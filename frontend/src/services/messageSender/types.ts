/**
 * 统一发送消息 - 类型定义
 */

import type { Message, GenerationParams } from '../message';

/** 媒体类型（可扩展） */
export type MediaType = 'chat' | 'image' | 'video';

/** 发送消息回调 */
export interface SendMessageCallbacks {
  /** 消息待处理（乐观更新） */
  onMessagePending: (message: Message) => void;
  /** 消息发送完成（成功或失败） */
  onMessageSent: (aiMessage?: Message | null) => void;
  /** 流式内容更新（仅聊天） */
  onStreamContent?: (text: string, conversationId: string) => void;
  /** 流式开始（仅聊天） */
  onStreamStart?: (conversationId: string, modelId: string) => void;
  /** 媒体任务已提交（图片/视频） */
  onMediaTaskSubmitted?: () => void;
}

/** 发送消息基础参数 */
export interface SendMessageParams {
  /** 消息类型 */
  type: MediaType;
  /** 对话ID */
  conversationId: string;
  /** 消息内容 */
  content: string;
  /** 附带图片URL（可选） */
  imageUrl?: string | null;

  /** 模型ID */
  modelId: string;
  /** 生成参数（图片/视频需要） */
  generationParams?: GenerationParams;

  /** 对话标题（媒体任务需要） */
  conversationTitle?: string;

  /** 客户端请求ID（可选，用于去重） */
  clientRequestId?: string;

  /** 回调函数 */
  callbacks: SendMessageCallbacks;
}

/** 聊天特有参数 */
export interface ChatSenderParams extends SendMessageParams {
  type: 'chat';
  /** 思考力度 */
  thinkingEffort?: 'minimal' | 'low' | 'medium' | 'high';
  /** 深度思考模式 */
  deepThinkMode?: boolean;
  /** 跳过乐观更新（重新生成时用） */
  skipOptimisticUpdate?: boolean;
}

/** 图片特有参数 */
export interface ImageSenderParams extends SendMessageParams {
  type: 'image';
  /** 必须有图片生成参数 */
  generationParams: GenerationParams & { image: NonNullable<GenerationParams['image']> };
}

/** 视频特有参数 */
export interface VideoSenderParams extends SendMessageParams {
  type: 'video';
  /** 必须有视频生成参数 */
  generationParams: GenerationParams & { video: NonNullable<GenerationParams['video']> };
}
