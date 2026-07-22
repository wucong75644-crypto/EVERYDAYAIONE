/** 消息发送的乐观状态、响应替换和错误回滚。 */

import { getPlaceholderText } from '../constants/placeholder';
import { useMessageStore, type ContentPart, type Message } from '../stores/useMessageStore';
import { logger } from '../utils/logger';
import { toApiRequestError } from './api';

export type GenerationType = 'chat' | 'image' | 'image_ecom' | 'video' | 'audio';
export type MessageOperation = 'send' | 'regenerate' | 'retry' | 'regenerate_single';

export interface SendOptions {
  conversationId: string;
  content: ContentPart[];
  generationType?: GenerationType;
  model?: string;
  params?: Record<string, unknown>;
  operation?: MessageOperation;
  originalMessageId?: string;
  subscribeTask?: (taskId: string, conversationId: string) => void;
  unsubscribeTask?: (taskId: string) => void;
  identifiers?: SendIdentifiers;
}

export interface SendIdentifiers {
  clientRequestId: string;
  userMessageId: string;
  assistantMessageId: string;
  clientTaskId: string;
}

export interface GenerateResponse {
  task_id: string;
  user_message: Message | null;
  assistant_message: Message;
  operation: MessageOperation;
  generation_type?: GenerationType;
}

export interface SendContext {
  clientRequestId: string;
  userMessageId: string;
  assistantMessageId: string;
  clientTaskId: string;
  now: Date;
  placeholderCreatedAt: string;
  originalAssistant?: Message;
}

/** 区分明确拒绝、已记录失败和结果未知，避免未知结果被错误回滚。 */
export function getSendFailureDisposition(
  error: unknown,
): 'rejected' | 'recorded_failure' | 'uncertain' {
  const apiError = toApiRequestError(error);
  if (apiError.code === 'IMAGE_GENERATION_FAILED') return 'recorded_failure';
  if (apiError.transport === 'timeout' || apiError.transport === 'network') return 'uncertain';
  if (apiError.status === 409 && apiError.code === 'IDEMPOTENCY_REQUEST_IN_PROGRESS') {
    return 'uncertain';
  }
  if (apiError.transport === 'http' && apiError.status === 500) return 'rejected';
  if (apiError.status !== undefined && apiError.status >= 500) return 'uncertain';
  return 'rejected';
}

/** Phase 1：创建用户乐观消息与助手占位符。 */
export function applyOptimisticUpdate(options: SendOptions, ctx: SendContext): void {
  const { conversationId, content, generationType, model, params, operation = 'send' } = options;
  const store = useMessageStore.getState();

  if (operation !== 'retry' && operation !== 'regenerate_single') {
    store.addMessage(conversationId, {
      id: ctx.userMessageId,
      conversation_id: conversationId,
      role: 'user',
      content,
      status: 'completed',
      created_at: ctx.now.toISOString(),
      client_request_id: ctx.clientRequestId,
    });
  }

  if (operation === 'retry') {
    store.updateMessage(ctx.assistantMessageId, {
      status: 'pending',
      content: [],
      is_error: false,
      error: undefined,
    });
    if (generationType === 'chat' || !generationType) {
      store.registerStreamingId(conversationId, ctx.assistantMessageId);
    } else {
      store.setIsSending(true);
    }
    return;
  }

  if (operation === 'regenerate_single') {
    const imageIndex = (params?.image_index as number) ?? 0;
    const existing = store.getMessage(ctx.assistantMessageId);
    if (existing) {
      const contentCopy = [...existing.content];
      if (imageIndex < contentCopy.length) {
        contentCopy[imageIndex] = { type: 'image', url: null } as ContentPart;
      }
      store.updateMessage(ctx.assistantMessageId, {
        content: contentCopy,
        status: 'pending',
      });
    }
    store.setIsSending(true);
    return;
  }

  store.startStreaming(conversationId, ctx.assistantMessageId, {
    initialContent: '',
    createdAt: ctx.placeholderCreatedAt,
    generationParams: { model },
  });
}

/** Phase 3-5：替换占位状态、建立任务追踪并校验 task_id。 */
export function processApiResponse(
  response: GenerateResponse,
  options: SendOptions,
  ctx: SendContext,
): void {
  const { conversationId, generationType, operation = 'send', subscribeTask } = options;
  const store = useMessageStore.getState();

  store.updateMessage(ctx.assistantMessageId, { task_id: response.task_id });

  const actualType = response.generation_type;
  if (actualType && actualType !== 'chat' && operation === 'retry') {
    replaceWithMediaPlaceholder(
      response,
      options,
      ctx,
      actualType === 'image_ecom' ? 'image' : actualType,
    );
  } else if (actualType && operation !== 'retry' && operation !== 'regenerate_single') {
    if (actualType === 'chat') {
      store.updateMessage(ctx.assistantMessageId, {
        generation_params: response.assistant_message.generation_params,
      });
    } else if (actualType === 'image_ecom') {
      const hasTaskMeta = !!options.params?.image_task_meta;
      if (hasTaskMeta) {
        replaceWithMediaPlaceholder(response, options, ctx, 'image');
      } else {
        store.updateMessage(ctx.assistantMessageId, {
          generation_params: response.assistant_message.generation_params,
        });
      }
    } else if (actualType === 'image' || actualType === 'video' || actualType === 'audio') {
      replaceWithMediaPlaceholder(response, options, ctx, actualType);
    }
  }

  store.createTask({
    taskId: ctx.clientTaskId,
    messageId: response.assistant_message.id,
    conversationId,
    type: actualType || generationType || 'chat',
    status: 'pending',
    progress: 0,
    createdAt: Date.now(),
  });

  if (response.task_id !== ctx.clientTaskId) {
    logger.warn('messageSender', 'task_id mismatch', {
      expected: ctx.clientTaskId,
      received: response.task_id,
    });
    subscribeTask?.(response.task_id, conversationId);
  }
}

function replaceWithMediaPlaceholder(
  response: GenerateResponse,
  options: SendOptions,
  ctx: SendContext,
  mediaType: 'image' | 'video' | 'audio',
): void {
  const render = response.assistant_message.generation_params?._render as Record<string, string> | undefined;
  const loadingText = render?.placeholder_text || getPlaceholderText(mediaType);
  const store = useMessageStore.getState();
  store.completeStreamingWithMessage(options.conversationId, {
    id: ctx.assistantMessageId,
    conversation_id: options.conversationId,
    role: 'assistant',
    content: [{ type: 'text', text: loadingText }],
    status: 'pending',
    created_at: ctx.placeholderCreatedAt,
    generation_params: response.assistant_message.generation_params,
    task_id: response.task_id,
  });
  store.setIsSending(true);
}

/** 发送失败时恢复原消息或构造统一失败状态。 */
export function rollbackOnError(
  error: unknown,
  options: SendOptions,
  ctx: SendContext,
): void {
  const { conversationId, generationType, params, operation = 'send', unsubscribeTask } = options;
  const store = useMessageStore.getState();
  const apiError = toApiRequestError(error);

  logger.error('messageSender', 'send failed', error);
  if (unsubscribeTask) {
    unsubscribeTask(ctx.clientTaskId);
    logger.info('messageSender', 'unsubscribed from task', {
      clientTaskId: ctx.clientTaskId,
    });
  }
  store.completeStreaming(conversationId);
  store.setIsSending(false);

  if (apiError.code === 'INSUFFICIENT_CREDITS') {
    if (ctx.originalAssistant) {
      store.updateMessage(ctx.assistantMessageId, ctx.originalAssistant);
    } else {
      store.removeMessage(ctx.assistantMessageId);
      store.removeMessage(ctx.userMessageId);
    }
    return;
  }

  if (apiError.code === 'IMAGE_GENERATION_FAILED' && generationType === 'image') {
    const imageCount = Math.max(1, Math.min(4, Number(params?.num_images ?? 1)));
    const failedPart = {
      type: 'image' as const,
      url: null,
      failed: true,
      error: apiError.message,
    };
    let failedContent: ContentPart[] = Array.from(
      { length: imageCount },
      () => ({ ...failedPart }),
    );
    if (operation === 'regenerate_single' && ctx.originalAssistant) {
      failedContent = [...ctx.originalAssistant.content];
      failedContent[Number(params?.image_index ?? 0)] = failedPart;
    }
    store.updateMessage(ctx.assistantMessageId, {
      content: failedContent,
      status: 'failed',
      is_error: false,
      error: { code: apiError.code, message: apiError.message },
      generation_params: {
        ...ctx.originalAssistant?.generation_params,
        ...params,
        type: 'image',
      },
    });
    return;
  }

  if (ctx.originalAssistant) {
    store.updateMessage(ctx.assistantMessageId, ctx.originalAssistant);
    return;
  }

  store.removeMessage(ctx.assistantMessageId);
  store.addMessage(conversationId, {
    id: ctx.assistantMessageId,
    conversation_id: conversationId,
    role: 'assistant',
    content: [{ type: 'text', text: '' }],
    status: 'failed',
    is_error: true,
    error: { code: 'SEND_FAILED', message: apiError.message },
    created_at: new Date().toISOString(),
  });
}
