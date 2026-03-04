/**
 * 统一消息发送器
 *
 * 简化设计：
 * 1. 单一入口：所有消息类型（chat/image/video）通过 sendMessage()
 * 2. 乐观更新：添加占位消息，后端确认后替换 ID
 * 3. WebSocket：完成通知由 WebSocketContext 处理
 *
 * 状态流转：
 * OPTIMISTIC (乐观更新) → PENDING (后端确认) → STREAMING/PROCESSING → COMPLETED
 */

import { request } from './api';
import { useMessageStore, type ContentPart, type Message } from '../stores/useMessageStore';
import { logger } from '../utils/logger';
import { getPlaceholderText } from '../constants/placeholder';

// ============================================================
// 类型定义
// ============================================================

/** 生成类型 */
export type GenerationType = 'chat' | 'image' | 'video' | 'audio';

/** 操作类型 */
export type MessageOperation = 'send' | 'regenerate' | 'retry' | 'regenerate_single';

/** 发送选项 */
export interface SendOptions {
  /** 对话 ID */
  conversationId: string;
  /** 消息内容（统一 ContentPart[] 格式） */
  content: ContentPart[];
  /** 生成类型（自动推断或显式指定） */
  generationType?: GenerationType;
  /** 模型 ID */
  model?: string;
  /** 类型特定参数 */
  params?: Record<string, unknown>;
  /** 操作类型 */
  operation?: MessageOperation;
  /** 原消息 ID（regenerate/retry 时传入） */
  originalMessageId?: string;
  /** WebSocket 订阅函数（可选，由调用者注入） */
  subscribeTask?: (taskId: string, conversationId: string) => void;
  /** WebSocket 取消订阅函数（可选，用于错误回滚） */
  unsubscribeTask?: (taskId: string) => void;
}

/** API 请求格式 */
interface GenerateRequest {
  operation: MessageOperation;
  content: ContentPart[];
  generation_type?: GenerationType;
  model?: string;
  params?: Record<string, unknown>;
  original_message_id?: string;
  client_request_id: string;
  created_at?: string;
  assistant_message_id?: string;
}

/** API 响应格式 */
interface GenerateResponse {
  task_id: string;
  user_message: Message | null;
  assistant_message: Message;
  operation: MessageOperation;
}

// ============================================================
// 主函数
// ============================================================

/**
 * 统一消息发送
 *
 * 使用方式：
 * ```ts
 * await sendMessage({
 *   conversationId: 'xxx',
 *   content: [{ type: 'text', text: 'Hello' }],
 *   generationType: 'chat',
 *   subscribeTask: (taskId) => ws.subscribe(taskId),
 * });
 * ```
 */
export async function sendMessage(options: SendOptions): Promise<string> {
  const {
    conversationId,
    content,
    generationType,
    model,
    params,
    operation = 'send',
    originalMessageId,
    subscribeTask,
    unsubscribeTask,
  } = options;

  const messageStore = useMessageStore.getState();
  const clientRequestId = crypto.randomUUID();
  const userMessageId = crypto.randomUUID();      // 用户消息真实 UUID
  // retry/regenerate_single 时复用原消息 ID，其他操作生成新 UUID
  const assistantMessageId = (operation === 'retry' || operation === 'regenerate_single') && originalMessageId
    ? originalMessageId
    : crypto.randomUUID();
  const now = new Date();

  // 🔥 关键改动：生成 client_task_id（用于提前订阅）
  const clientTaskId = crypto.randomUUID();

  logger.info('messageSender', 'sending message', {
    conversationId,
    operation,
    generationType,
    clientRequestId,
    clientTaskId,  // 🔥 新增日志
  });

  // ========================================
  // Phase 1: 乐观更新（使用真实 UUID）
  // ========================================

  // 1.1 创建乐观用户消息（send/regenerate，retry/regenerate_single 不创建）
  if (operation !== 'retry' && operation !== 'regenerate_single') {
    const userMessage: Message = {
      id: userMessageId,
      conversation_id: conversationId,
      role: 'user',
      content,
      status: 'completed',
      created_at: now.toISOString(),
      client_request_id: clientRequestId,
    };

    messageStore.addMessage(conversationId, userMessage);
  }

  // 1.2 处理助手消息（retry 更新原消息，其他创建新占位符）
  const placeholderCreatedAt = new Date(now.getTime() + 1).toISOString();

  if (operation === 'retry') {
    // retry: 原地更新消息状态（保持消息在列表中的位置）
    messageStore.updateMessage(assistantMessageId, {
      status: 'pending',
      content: [],
      is_error: false,
      error: undefined,
    });

    if (generationType === 'chat' || !generationType) {
      // Chat 类型：只注册 streamingId，不创建新消息
      // 这样 message_chunk 能路由到正确的消息，且不会创建重复消息
      messageStore.registerStreamingId(conversationId, assistantMessageId);
    } else {
      // Media 类型：只设置发送状态
      messageStore.setIsSending(true);
    }
  } else if (operation === 'regenerate_single') {
    // regenerate_single: 仅将 content[image_index] 设为 null 占位符
    const imageIndex = (params?.image_index as number) ?? 0;
    const existing = messageStore.getMessage(assistantMessageId);
    if (existing) {
      const newContent = [...existing.content];
      if (imageIndex < newContent.length) {
        newContent[imageIndex] = { type: 'image', url: null } as ContentPart;
      }
      messageStore.updateMessage(assistantMessageId, { content: newContent, status: 'pending' });
    }
    messageStore.setIsSending(true);
  } else if (generationType === 'chat' || !generationType) {
    // Chat 类型：使用 startStreaming 创建占位符
    // 这样 streamingMessages Map 会正确设置，message_chunk 能路由到正确的消息
    messageStore.startStreaming(conversationId, assistantMessageId, {
      initialContent: '',
      createdAt: placeholderCreatedAt,
      generationParams: { type: generationType, model },
    });
  } else {
    // Media 类型：直接添加到 messages（不再使用 optimistic，避免状态分散）
    // 占位符将在原地被替换，不会触发滚动
    const loadingText = getPlaceholderText(generationType as 'image' | 'video');

    const placeholderMessage: Message = {
      id: assistantMessageId,
      conversation_id: conversationId,
      role: 'assistant',
      content: [{ type: 'text', text: loadingText }],
      status: 'pending',
      created_at: placeholderCreatedAt,
      generation_params: {
        type: generationType,
        model,
        ...(generationType === 'image' && params?.num_images ? { num_images: params.num_images } : {}),
      },
    };
    messageStore.addMessage(conversationId, placeholderMessage); // 直接添加到 messages
    messageStore.setIsSending(true);
  }

  // ========================================
  // 🔥 Phase 1.5: 提前订阅（在发送请求前）
  // ========================================

  if (subscribeTask) {
    subscribeTask(clientTaskId, conversationId);
    logger.info('messageSender', 'pre-subscribed to task', { clientTaskId });
  }

  try {
    // ========================================
    // Phase 2: 调用后端 API
    // ========================================

    const response = await request<GenerateResponse>({
      url: `/conversations/${conversationId}/messages/generate`,
      method: 'POST',
      timeout: 60000, // 生成请求需要更长超时（KIE 等 Provider 响应慢）
      data: {
        operation,
        content,
        generation_type: generationType,
        model,
        params,
        original_message_id: originalMessageId,
        client_request_id: clientRequestId,
        client_task_id: clientTaskId, // 🔥 前端生成的 task_id（用于订阅）
        created_at: now.toISOString(),
        assistant_message_id: assistantMessageId, // 前端生成的真实 UUID
        placeholder_created_at: placeholderCreatedAt, // 占位符的创建时间（确保前后端一致）
      } as GenerateRequest,
    });

    logger.info('messageSender', 'API response received', {
      taskId: response.task_id,
      userMessageId: response.user_message?.id,
      assistantMessageId: response.assistant_message.id,
    });

    // ========================================
    // Phase 3: 更新消息状态（ID已一致，无需替换）
    // ========================================

    // 3.1 更新助手消息的 task_id
    messageStore.updateMessage(assistantMessageId, {
      task_id: response.task_id,
    });

    // ========================================
    // Phase 4: 创建任务追踪
    // ========================================

    messageStore.createTask({
      taskId: clientTaskId, // 🔥 使用 clientTaskId（已订阅）
      messageId: response.assistant_message.id,
      conversationId,
      type: generationType || 'chat',
      status: 'pending',
      progress: 0,
      createdAt: Date.now(),
    });

    // ========================================
    // Phase 5: 验证后端返回的 task_id（应该与 clientTaskId 一致）
    // ========================================

    if (response.task_id !== clientTaskId) {
      logger.warn('messageSender', 'task_id mismatch', {
        expected: clientTaskId,
        received: response.task_id,
      });
      // 如果不一致，说明后端不支持 client_task_id，需要补订阅
      if (subscribeTask) {
        subscribeTask(response.task_id, conversationId);
      }
    }

    logger.info('messageSender', 'message sent successfully', {
      taskId: clientTaskId,
      backendTaskId: response.task_id,
    });

    return clientTaskId; // 🔥 返回 clientTaskId（前端已订阅）

  } catch (error) {
    // ========================================
    // 错误处理：回滚乐观更新
    // ========================================

    logger.error('messageSender', 'send failed', error);

    // 🔥 取消订阅（防止内存泄漏）
    if (unsubscribeTask) {
      unsubscribeTask(clientTaskId);
      logger.info('messageSender', 'unsubscribed from task', { clientTaskId });
    }

    // 清理 streamingMessages（Chat 类型会设置）
    messageStore.completeStreaming(conversationId);

    // 🔥 清理发送状态（修复光标持续闪动问题）
    messageStore.setIsSending(false);

    // 移除占位符（同时检查 messages 和 optimisticMessages）
    messageStore.removeMessage(assistantMessageId);

    // 添加错误消息
    const errorMessage: Message = {
      id: assistantMessageId,
      conversation_id: conversationId,
      role: 'assistant',
      content: [{ type: 'text', text: '' }],
      status: 'failed',
      is_error: true,
      error: {
        code: 'SEND_FAILED',
        message: error instanceof Error ? error.message : '发送失败',
      },
      created_at: new Date().toISOString(),
    };

    messageStore.addMessage(conversationId, errorMessage);

    throw error;
  }
}

// ============================================================
// 辅助函数
// ============================================================

/**
 * 创建错误消息（供 handler 使用）
 */
export function createErrorMessage(
  conversationId: string,
  error: unknown,
  defaultText = '发送失败'
): Message {
  return {
    id: crypto.randomUUID(),
    conversation_id: conversationId,
    role: 'assistant',
    content: [{ type: 'text', text: error instanceof Error ? error.message : defaultText }],
    status: 'failed',
    is_error: true,
    created_at: new Date().toISOString(),
  };
}

/**
 * 创建文本消息内容
 */
export function createTextContent(text: string): ContentPart[] {
  return [{ type: 'text', text }];
}

/**
 * 创建图文混合内容（多图）
 */
export function createTextWithImages(text: string, imageUrls: string[]): ContentPart[] {
  return [
    { type: 'text', text },
    ...imageUrls.map(url => ({ type: 'image' as const, url })),
  ];
}

/**
 * 从 ContentPart[] 提取文本
 */
export function getTextFromContent(content: ContentPart[]): string {
  for (const part of content) {
    if (part.type === 'text') {
      return part.text;
    }
  }
  return '';
}

/**
 * 推断生成类型
 */
export function inferGenerationType(content: ContentPart[]): GenerationType {
  const text = getTextFromContent(content).toLowerCase();

  // 图片生成关键词
  if (/生成图片|画一|generate image|\/image/i.test(text)) {
    return 'image';
  }

  // 视频生成关键词
  if (/生成视频|做个视频|generate video|\/video/i.test(text)) {
    return 'video';
  }

  // 默认聊天
  return 'chat';
}

/**
 * 判断消息类型（用于重新生成）
 */
export function determineMessageType(message: Message): GenerationType {
  // 优先从 generation_params 判断
  if (message.generation_params?.type) {
    return message.generation_params.type as GenerationType;
  }

  // 从内容判断
  for (const part of message.content) {
    if (part.type === 'video') return 'video';
    if (part.type === 'image') return 'image';
  }

  return 'chat';
}

/**
 * 提取模型 ID（用于重新生成）
 */
export function extractModelId(message: Message): string | undefined {
  return message.generation_params?.model as string | undefined;
}

/**
 * 提取生成参数（用于重新生成，保持原参数）
 * 返回的参数已转换为后端期望的下划线格式
 */
export function extractGenerationParams(message: Message): Record<string, unknown> {
  const params: Record<string, unknown> = {};
  const gp = message.generation_params;

  if (!gp) return params;

  // 聊天参数
  if (gp.thinking_effort) params.thinking_effort = gp.thinking_effort;
  if (gp.thinking_mode) params.thinking_mode = gp.thinking_mode;

  // 图片参数
  if (gp.aspect_ratio) params.aspect_ratio = gp.aspect_ratio;
  if (gp.resolution) params.resolution = gp.resolution;
  if (gp.output_format) params.output_format = gp.output_format;
  if (gp.num_images) params.num_images = gp.num_images;

  // 视频参数
  if (gp.n_frames) params.n_frames = gp.n_frames;
  if (gp.remove_watermark !== undefined) params.remove_watermark = gp.remove_watermark;

  return params;
}
