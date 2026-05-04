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
export type GenerationType = 'chat' | 'image' | 'image_ecom' | 'video' | 'audio';

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
  generation_type?: GenerationType;
}

/** 发送上下文（sendMessage 内部生成的 ID 和时间戳） */
interface SendContext {
  clientRequestId: string;
  userMessageId: string;
  assistantMessageId: string;
  clientTaskId: string;
  now: Date;
  placeholderCreatedAt: string;
}

// ============================================================
// Phase 提取函数
// ============================================================

/** Phase 1: 乐观更新 — 创建用户消息 + 助手占位符 */
function applyOptimisticUpdate(options: SendOptions, ctx: SendContext): void {
  const { conversationId, content, generationType, model, params, operation = 'send' } = options;
  const messageStore = useMessageStore.getState();

  // 1.1 创建乐观用户消息（send/regenerate，retry/regenerate_single 不创建）
  if (operation !== 'retry' && operation !== 'regenerate_single') {
    messageStore.addMessage(conversationId, {
      id: ctx.userMessageId,
      conversation_id: conversationId,
      role: 'user',
      content,
      status: 'completed',
      created_at: ctx.now.toISOString(),
      client_request_id: ctx.clientRequestId,
    });
  }

  // 1.2 处理助手消息（retry 更新原消息，其他创建新占位符）
  if (operation === 'retry') {
    messageStore.updateMessage(ctx.assistantMessageId, {
      status: 'pending',
      content: [],
      is_error: false,
      error: undefined,
    });
    if (generationType === 'chat' || !generationType) {
      messageStore.registerStreamingId(conversationId, ctx.assistantMessageId);
    } else {
      messageStore.setIsSending(true);
    }
  } else if (operation === 'regenerate_single') {
    const imageIndex = (params?.image_index as number) ?? 0;
    const existing = messageStore.getMessage(ctx.assistantMessageId);
    if (existing) {
      const newContent = [...existing.content];
      if (imageIndex < newContent.length) {
        newContent[imageIndex] = { type: 'image', url: null } as ContentPart;
      }
      messageStore.updateMessage(ctx.assistantMessageId, { content: newContent, status: 'pending' });
    }
    messageStore.setIsSending(true);
  } else {
    // 统一占位符：旋转圆点（思考阶段），HTTP 响应后变形为类型专属占位符
    messageStore.startStreaming(conversationId, ctx.assistantMessageId, {
      initialContent: '',
      createdAt: ctx.placeholderCreatedAt,
      generationParams: { model },
    });
  }
}

/** Phase 3-5: API 响应处理 — 状态更新 + 任务创建 + task_id 验证 */
function processApiResponse(
  response: GenerateResponse,
  options: SendOptions,
  ctx: SendContext,
): void {
  const { conversationId, generationType, operation = 'send', subscribeTask } = options;
  const messageStore = useMessageStore.getState();

  // 3.1 更新助手消息的 task_id
  messageStore.updateMessage(ctx.assistantMessageId, { task_id: response.task_id });

  // 3.2 占位符变形：旋转圆点 → 类型专属占位符
  const actualType = response.generation_type;
  if (actualType && operation !== 'retry' && operation !== 'regenerate_single') {
    if (actualType === 'chat') {
      messageStore.updateMessage(ctx.assistantMessageId, {
        generation_params: response.assistant_message.generation_params,
      });
    } else if (actualType === 'image' || actualType === 'image_ecom' || actualType === 'video' || actualType === 'audio') {
      const placeholderType = actualType === 'image_ecom' ? 'image' : actualType;
      const render = response.assistant_message?.generation_params?._render as Record<string, string> | undefined;
      const loadingText = render?.placeholder_text || getPlaceholderText(placeholderType as 'image' | 'video' | 'audio');
      messageStore.completeStreamingWithMessage(conversationId, {
        id: ctx.assistantMessageId,
        conversation_id: conversationId,
        role: 'assistant',
        content: [{ type: 'text', text: loadingText }],
        status: 'pending',
        created_at: ctx.placeholderCreatedAt,
        generation_params: response.assistant_message.generation_params,
        task_id: response.task_id,
      });
      messageStore.setIsSending(true);
    }
  }

  // Phase 4: 创建任务追踪
  messageStore.createTask({
    taskId: ctx.clientTaskId,
    messageId: response.assistant_message.id,
    conversationId,
    type: actualType || generationType || 'chat',
    status: 'pending',
    progress: 0,
    createdAt: Date.now(),
  });

  // Phase 5: 验证 task_id 一致性
  if (response.task_id !== ctx.clientTaskId) {
    logger.warn('messageSender', 'task_id mismatch', {
      expected: ctx.clientTaskId,
      received: response.task_id,
    });
    if (subscribeTask) {
      subscribeTask(response.task_id, conversationId);
    }
  }
}

/** 错误回滚 — 取消订阅 + 清理状态 + 添加错误消息 */
function rollbackOnError(error: unknown, options: SendOptions, ctx: SendContext): void {
  const { conversationId, unsubscribeTask } = options;
  const messageStore = useMessageStore.getState();

  logger.error('messageSender', 'send failed', error);

  if (unsubscribeTask) {
    unsubscribeTask(ctx.clientTaskId);
    logger.info('messageSender', 'unsubscribed from task', { clientTaskId: ctx.clientTaskId });
  }

  messageStore.completeStreaming(conversationId);
  messageStore.setIsSending(false);
  messageStore.removeMessage(ctx.assistantMessageId);

  messageStore.addMessage(conversationId, {
    id: ctx.assistantMessageId,
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
  });
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
  const { conversationId, content, generationType, model, params,
    operation = 'send', originalMessageId, subscribeTask } = options;

  // 生成上下文 ID 和时间戳
  const now = new Date();
  const ctx: SendContext = {
    clientRequestId: crypto.randomUUID(),
    userMessageId: crypto.randomUUID(),
    assistantMessageId: (operation === 'retry' || operation === 'regenerate_single') && originalMessageId
      ? originalMessageId
      : crypto.randomUUID(),
    clientTaskId: crypto.randomUUID(),
    now,
    placeholderCreatedAt: new Date(now.getTime() + 1).toISOString(),
  };

  logger.info('messageSender', 'sending message', {
    conversationId, operation, generationType,
    clientRequestId: ctx.clientRequestId, clientTaskId: ctx.clientTaskId,
  });

  // Phase 1: 乐观更新
  applyOptimisticUpdate(options, ctx);

  // Phase 1.5: 提前订阅（在发送请求前）
  if (subscribeTask) {
    subscribeTask(ctx.clientTaskId, conversationId);
    logger.info('messageSender', 'pre-subscribed to task', { clientTaskId: ctx.clientTaskId });
  }

  try {
    // Phase 2: 调用后端 API
    const response = await request<GenerateResponse>({
      url: `/conversations/${conversationId}/messages/generate`,
      method: 'POST',
      timeout: 60000,
      data: {
        operation, content, generation_type: generationType,
        model, params, original_message_id: originalMessageId,
        client_request_id: ctx.clientRequestId,
        client_task_id: ctx.clientTaskId,
        created_at: ctx.now.toISOString(),
        assistant_message_id: ctx.assistantMessageId,
        placeholder_created_at: ctx.placeholderCreatedAt,
      } as GenerateRequest,
    });

    logger.info('messageSender', 'API response received', {
      taskId: response.task_id,
      userMessageId: response.user_message?.id,
      assistantMessageId: response.assistant_message.id,
    });

    // Phase 3-5: 状态更新 + 任务创建 + task_id 验证
    processApiResponse(response, options, ctx);

    logger.info('messageSender', 'message sent successfully', {
      taskId: ctx.clientTaskId, backendTaskId: response.task_id,
    });

    return ctx.clientTaskId;

  } catch (error) {
    rollbackOnError(error, options, ctx);
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
 * 创建带文件（PDF）的混合内容
 */
export function createTextWithFiles(
  text: string,
  imageUrls: string[] | null,
  files: { url: string; name: string; mime_type: string; size: number; workspace_path?: string }[],
): ContentPart[] {
  return [
    { type: 'text', text },
    ...(imageUrls || []).map(url => ({ type: 'image' as const, url })),
    ...files.map(f => ({
      type: 'file' as const,
      url: f.url,
      name: f.name,
      mime_type: f.mime_type,
      size: f.size,
      ...(f.workspace_path ? { workspace_path: f.workspace_path } : {}),
    })),
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
