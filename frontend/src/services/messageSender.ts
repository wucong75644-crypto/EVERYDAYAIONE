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

import { ApiRequestError, request, toApiRequestError } from './api';
import { useMessageStore, type ContentPart, type Message } from '../stores/useMessageStore';
import { logger } from '../utils/logger';
import { pickOriginalImageUrl, toOriginalImageUrl } from '../utils/imageUrlRules';
import {
  applyOptimisticUpdate,
  processApiResponse,
  rollbackOnError,
  getSendFailureDisposition,
  type GenerateResponse,
  type GenerationType,
  type MessageOperation,
  type SendContext,
  type SendOptions,
} from './messageSendLifecycle';

export type { GenerationType, MessageOperation, SendOptions } from './messageSendLifecycle';

const RETRY_DELAYS_MS = [500, 1500] as const;

// ============================================================
// 类型定义
// ============================================================

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

/** Phase 1: 乐观更新 — 创建用户消息 + 助手占位符 */
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
  const identifiers = options.identifiers;
  const ctx: SendContext = {
    clientRequestId: identifiers?.clientRequestId ?? crypto.randomUUID(),
    userMessageId: identifiers?.userMessageId ?? crypto.randomUUID(),
    assistantMessageId: identifiers?.assistantMessageId ?? (
      (operation === 'retry' || operation === 'regenerate_single') && originalMessageId
        ? originalMessageId
        : crypto.randomUUID()
    ),
    clientTaskId: identifiers?.clientTaskId ?? crypto.randomUUID(),
    now,
    placeholderCreatedAt: new Date(now.getTime() + 1).toISOString(),
  };
  if ((operation === 'retry' || operation === 'regenerate_single') && originalMessageId) {
    const original = useMessageStore.getState().getMessage(originalMessageId);
    if (original) ctx.originalAssistant = structuredClone(original);
  }

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
    const response = await requestWithIdempotentRetry({
      url: `/conversations/${conversationId}/messages/generate`,
      method: 'POST',
      timeout: 60000,
      headers: { 'Idempotency-Key': ctx.clientRequestId },
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
    const apiError = error instanceof ApiRequestError ? error : toApiRequestError(error);
    const disposition = getSendFailureDisposition(apiError);
    const classifiedError = new ApiRequestError(
      apiError.code, apiError.message, apiError.status, apiError.details,
      apiError.transport, disposition,
    );
    if (disposition !== 'uncertain') rollbackOnError(classifiedError, options, ctx);
    throw classifiedError;
  }
}

interface GenerateRequestConfig {
  url: string;
  method: 'POST';
  timeout: number;
  headers: Record<string, string>;
  data: GenerateRequest;
}

async function requestWithIdempotentRetry(
  config: GenerateRequestConfig,
): Promise<GenerateResponse> {
  for (let attempt = 0; ; attempt += 1) {
    try {
      return await request<GenerateResponse>(config);
    } catch (error) {
      const apiError = toApiRequestError(error);
      if (attempt >= RETRY_DELAYS_MS.length || !isSafelyRetryable(apiError)) throw apiError;
      const delay = apiError.retryAfterMs ?? RETRY_DELAYS_MS[attempt];
      await new Promise<void>(resolve => setTimeout(resolve, delay));
    }
  }
}

function isSafelyRetryable(error: ApiRequestError): boolean {
  if (error.transport === 'timeout' || error.transport === 'network') return true;
  if (error.code === 'IDEMPOTENCY_REQUEST_IN_PROGRESS' && error.status === 409) return true;
  if (error.code !== 'API_ERROR') return false;
  return error.status === 502 || error.status === 503 || error.status === 504;
}

// ============================================================
// 辅助函数
// ============================================================

/**
 * 创建文本消息内容
 */
export function createTextContent(text: string): ContentPart[] {
  return [{ type: 'text', text }];
}

/** 用户上传/引用图片的完整元数据（构造 ImagePart 时使用） */
export interface ImageInputInfo {
  url: string;
  original_url?: string;
  thumbnail_url?: string;
  preview_url?: string;
  download_url?: string;
  asset_id?: string;
  /** 工作区文件名，有值时后端注册 file_path_cache */
  name?: string;
  /** 工作区相对路径（如 上传/2026-06/xxx.png） */
  workspace_path?: string;
  mime_type?: string;
  size?: number;
  width?: number;
  height?: number;
}

function createOriginalImagePart(url: string): ContentPart {
  return {
    type: 'image',
    url: toOriginalImageUrl(url),
  };
}

/**
 * 创建图文混合内容（多图）
 *
 * 接收字符串数组（旧接口，仅 url）或对象数组（含 workspace_path/name 等元数据）。
 * 对象数组时 ImagePart 会带上 name + workspace_path，后端可注册到 file_path_cache
 * 并在 <attachments> XML 块中展示文件名给 LLM。
 */
export function createTextWithImages(
  text: string,
  images: string[] | ImageInputInfo[],
): ContentPart[] {
  const normalize = (img: string | ImageInputInfo) =>
    typeof img === 'string'
      ? createOriginalImagePart(img)
      : {
          type: 'image' as const,
          url: pickOriginalImageUrl(img.url, img.original_url, img.download_url, img.preview_url),
          original_url: pickOriginalImageUrl(img.original_url, img.download_url, img.preview_url, img.url),
          ...(img.thumbnail_url ? { thumbnail_url: img.thumbnail_url } : {}),
          preview_url: pickOriginalImageUrl(img.preview_url, img.original_url, img.download_url, img.url),
          download_url: pickOriginalImageUrl(img.download_url, img.original_url, img.preview_url, img.url),
          ...(img.asset_id ? { asset_id: img.asset_id } : {}),
          ...(img.name ? { name: img.name } : {}),
          ...(img.workspace_path ? { workspace_path: img.workspace_path } : {}),
          ...(img.mime_type ? { mime_type: img.mime_type } : {}),
          ...(img.size ? { size: img.size } : {}),
          ...(img.width ? { width: img.width } : {}),
          ...(img.height ? { height: img.height } : {}),
        };
  return [
    { type: 'text', text },
    ...(images as Array<string | ImageInputInfo>).map(normalize),
  ];
}

/**
 * 创建带文件（PDF）的混合内容
 *
 * imageUrls 既接受字符串数组（旧调用）也接受对象数组（含 workspace_path 等）。
 */
export function createTextWithFiles(
  text: string,
  imageUrls: string[] | ImageInputInfo[] | null,
  files: { url: string; name: string; mime_type: string; size: number; workspace_path?: string }[],
): ContentPart[] {
  const normalizeImg = (img: string | ImageInputInfo) =>
    typeof img === 'string'
      ? createOriginalImagePart(img)
      : {
          type: 'image' as const,
          url: pickOriginalImageUrl(img.url, img.original_url, img.download_url, img.preview_url),
          original_url: pickOriginalImageUrl(img.original_url, img.download_url, img.preview_url, img.url),
          ...(img.thumbnail_url ? { thumbnail_url: img.thumbnail_url } : {}),
          preview_url: pickOriginalImageUrl(img.preview_url, img.original_url, img.download_url, img.url),
          download_url: pickOriginalImageUrl(img.download_url, img.original_url, img.preview_url, img.url),
          ...(img.asset_id ? { asset_id: img.asset_id } : {}),
          ...(img.name ? { name: img.name } : {}),
          ...(img.workspace_path ? { workspace_path: img.workspace_path } : {}),
          ...(img.mime_type ? { mime_type: img.mime_type } : {}),
          ...(img.size ? { size: img.size } : {}),
          ...(img.width ? { width: img.width } : {}),
          ...(img.height ? { height: img.height } : {}),
        };
  const images = (imageUrls || []) as Array<string | ImageInputInfo>;
  return [
    { type: 'text', text },
    ...images.map(normalizeImg),
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
