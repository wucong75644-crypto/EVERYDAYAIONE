/** WebSocket 任务完成、失败与图片 partial update 处理。 */

import toast from 'react-hot-toast';
import { normalizeMessage, type Message } from '../stores/useMessageStore';
import { logger } from '../utils/logger';
import { tabSync } from '../utils/tabSync';
import { parseContentPart } from '../schemas/messageProtocol';
import type { ContentPart, ImagePart, RuntimeMediaSlotStatus } from '../types/message';
import {
  findImagePartContentIndex,
  findRuntimeMediaSlotContentIndex,
  getRuntimeMediaImageSlots,
  isRuntimeMediaImageSlot,
} from '../utils/runtimeMediaSlots';
import {
  cleanupTaskSubscription,
  flushChunkBuffer,
  type HandlerDeps,
  type WSIncomingMessage,
} from './wsMessageHandlerShared';

type NormalizeInput = Parameters<typeof normalizeMessage>[0];

function handleTaskDoneWithMessage(
  deps: HandlerDeps,
  taskId: string,
  messageData: Record<string, unknown>,
  conversationId: string,
): boolean {
  const store = deps.getStore();
  const normalized = normalizeMessage(messageData as NormalizeInput);
  const existing = store.getMessage(normalized.id);
  const alreadyCompleted = existing?.status === 'completed';

  logger.info('ws:done', alreadyCompleted
    ? 'message already completed by stream_end, persisting DB data'
    : 'processing message', {
    taskId,
    conversationId,
    messageId: normalized.id,
  });

  const status = normalized.status === 'failed' ? 'failed' as const : 'completed' as const;
  const updateData = { ...normalized, status };
  store.updateMessage(normalized.id, updateData);
  store.addMessage(conversationId, updateData);

  if (status === 'failed') {
    store.failTask(taskId, '生成失败');
  } else {
    store.completeTask(taskId);
  }

  const context = deps.operationContextRef.current.get(taskId);
  context?.onComplete?.(normalized);
  deps.operationContextRef.current.delete(taskId);
  return !alreadyCompleted;
}

function handleTaskFailure(
  deps: HandlerDeps,
  taskId: string,
  error: { message?: string } | undefined,
): void {
  const message = error?.message || '生成失败';
  deps.getStore().failTask(taskId, message);
  const context = deps.operationContextRef.current.get(taskId);
  context?.onError?.(new Error(message));
  deps.operationContextRef.current.delete(taskId);
  cleanupTaskSubscription(deps, taskId);
}

function flushPendingChunks(deps: HandlerDeps): void {
  if (deps.chunkBufferRef.current.size === 0) return;
  if (deps.flushTimerRef.current) {
    clearTimeout(deps.flushTimerRef.current);
    deps.flushTimerRef.current = null;
  }
  flushChunkBuffer(deps);
}

function completeConversation(
  deps: HandlerDeps,
  conversationId: string | undefined,
  messageId: string | undefined,
  isNewlyCompleted: boolean,
): void {
  if (!conversationId || !isNewlyCompleted) return;
  const store = deps.getStore();
  const ownsStreamingSlot = !!messageId
    && store.getStreamingMessageId(conversationId) === messageId;
  if (ownsStreamingSlot) {
    store.completeStreaming(conversationId);
  }
  store.markConversationCompleted(conversationId);
  if (ownsStreamingSlot) {
    store.setIsSending(false);
  }
  tabSync.broadcast('message_completed', { conversationId, messageId });
}

function notifyMessageDone(messageData: Record<string, unknown> | undefined, enabled: boolean): void {
  if (!enabled) return;
  const isFailed = messageData?.status === 'failed';
  if (isFailed) toast.error('生成失败');
  else toast.success('生成完成');
}

function notifyWorkspaceChanged(messageData: Record<string, unknown> | undefined): void {
  const content = messageData?.content;
  if (!Array.isArray(content)) return;
  const hasWorkspaceFile = content.some((part) => (
    part && typeof part === 'object' && typeof part.workspace_path === 'string'
  ));
  if (hasWorkspaceFile) window.dispatchEvent(new CustomEvent('workspace:changed'));
}

function finishMessageWithoutTask(
  deps: HandlerDeps,
  messageId: string | undefined,
  messageData: Record<string, unknown> | undefined,
): void {
  const store = deps.getStore();
  if (messageData) {
    const normalized = normalizeMessage(messageData as NormalizeInput);
    const status = normalized.status === 'failed' ? 'failed' as const : 'completed' as const;
    store.updateMessage(messageId || normalized.id, { ...normalized, status });
  } else if (messageId) {
    store.setStatus(messageId, 'completed');
  }
}

export function handleMessageDone(deps: HandlerDeps, msg: WSIncomingMessage): void {
  const { task_id, message_id, conversation_id } = msg;
  const messageData = (msg.message ?? msg.payload?.message) as Record<string, unknown> | undefined;
  const effectiveMessageId = message_id
    || (typeof messageData?.id === 'string' ? messageData.id : undefined);
  flushPendingChunks(deps);

  logger.info('ws:message', 'done received', {
    taskId: task_id,
    messageId: effectiveMessageId,
    conversationId: conversation_id,
  });

  const effectiveConversationId = conversation_id
    || (task_id ? deps.taskConversationMapRef.current.get(task_id) : undefined);
  let isNewlyCompleted = true;

  if (task_id) {
    if (messageData && effectiveConversationId) {
      isNewlyCompleted = handleTaskDoneWithMessage(
        deps,
        task_id,
        messageData,
        effectiveConversationId,
      );
    } else if (message_id) {
      const store = deps.getStore();
      store.setStatus(message_id, 'completed');
      store.completeTask(task_id);
    }
    cleanupTaskSubscription(deps, task_id);
  } else {
    finishMessageWithoutTask(deps, message_id, messageData);
  }

  completeConversation(deps, effectiveConversationId, effectiveMessageId, isNewlyCompleted);
  notifyMessageDone(messageData, isNewlyCompleted);
  notifyWorkspaceChanged(messageData);
}

function buildImageFailureContent(existing: Message, errorText: string, errorCode?: string): Message['content'] {
  const count = Math.max(1, Number(existing.generation_params?.num_images ?? 1));
  return Array.from({ length: count }, (_, index) => {
    const part = existing.content[index];
    if (part?.type === 'image' && part.url) return part;
    return { type: 'image' as const, url: null, failed: true, error: errorText, error_code: errorCode };
  });
}

function failMessage(
  deps: HandlerDeps,
  messageId: string,
  error: { code?: string; message?: string } | undefined,
): void {
  const store = deps.getStore();
  const errorText = error?.message || '生成失败';
  const existing = store.getMessage(messageId);
  if (existing?.generation_params?.type === 'image') {
    store.updateMessage(messageId, {
      status: 'failed', is_error: false,
      error: { code: error?.code ?? 'UNKNOWN', message: errorText },
      content: buildImageFailureContent(existing, errorText, error?.code),
    });
    return;
  }
  store.updateMessage(messageId, {
    status: 'failed', is_error: true,
    error: { code: error?.code ?? 'UNKNOWN', message: errorText },
    content: [{ type: 'text', text: errorText }],
  });
}

export function handleMessageError(deps: HandlerDeps, msg: WSIncomingMessage): void {
  const { task_id, message_id, conversation_id } = msg;
  const error = (msg.error ?? msg.payload?.error) as { code?: string; message?: string } | undefined;

  if (message_id) deps.chunkBufferRef.current.delete(message_id);
  if (deps.flushTimerRef.current && deps.chunkBufferRef.current.size === 0) {
    clearTimeout(deps.flushTimerRef.current);
    deps.flushTimerRef.current = null;
  }

  logger.error('ws:message', 'error received', undefined, {
    taskId: task_id,
    messageId: message_id,
    error,
  });

  const store = deps.getStore();
  if (message_id) failMessage(deps, message_id, error);

  if (task_id) handleTaskFailure(deps, task_id, error);
  const ownsStreamingSlot = !!conversation_id
    && !!message_id
    && store.getStreamingMessageId(conversation_id) === message_id;
  if (ownsStreamingSlot) {
    store.completeStreaming(conversation_id);
    store.setIsSending(false);
  }
  toast.error(error?.message || '生成失败');
}

export function handleImagePartialUpdate(
  deps: HandlerDeps,
  msg: WSIncomingMessage,
): void {
  const { message_id } = msg;
  const payload = (msg.payload || {}) as ImagePartialUpdatePayload;
  const {
    image_index, slot_index, completed_count, total_count, error,
  } = payload;
  if (!message_id || (image_index === undefined && slot_index === undefined && !payload.slot_id)) {
    return;
  }

  logger.info('ws:image', 'partial update', {
    messageId: message_id,
    imageIndex: image_index ?? slot_index,
    slotId: payload.slot_id,
    progress: `${completed_count}/${total_count}`,
    hasError: !!error,
  });

  const store = deps.getStore();
  const existing = store.getMessage(message_id);
  if (!existing) return;
  const update = getRuntimeMediaImageSlots(existing.content).length > 0
    || isRuntimeSlotPayload(payload)
    ? applyRuntimeSlotUpdate(existing.content, payload, message_id)
    : applyLegacyImageUpdate(existing.content, payload, message_id);
  if (!update) return;
  store.updateMessage(message_id, { content: update.content });

  if (update.updatedPart.workspace_path) {
    window.dispatchEvent(new CustomEvent('workspace:changed'));
  }
}

interface ImagePartialUpdatePayload {
  image_index?: number;
  slot_id?: string;
  slot_index?: number;
  slot_status?: RuntimeMediaSlotStatus;
  slot_revision?: number;
  content_part?: unknown;
  completed_count?: number;
  total_count?: number;
  error?: string;
  error_code?: string;
}

interface AppliedImageUpdate {
  content: ContentPart[];
  updatedPart: ImagePart;
}

function contentPartRecord(contentPart: unknown): Record<string, unknown> {
  return contentPart && typeof contentPart === 'object' && !Array.isArray(contentPart)
    ? contentPart as Record<string, unknown>
    : {};
}

function isRuntimeSlotPayload(payload: ImagePartialUpdatePayload): boolean {
  const rawPart = contentPartRecord(payload.content_part);
  return payload.slot_id !== undefined
    || payload.slot_index !== undefined
    || payload.slot_status !== undefined
    || payload.slot_revision !== undefined
    || rawPart.slot_id !== undefined
    || rawPart.slot_index !== undefined
    || rawPart.slot_status !== undefined
    || rawPart.slot_revision !== undefined;
}

function applyRuntimeSlotUpdate(
  original: ContentPart[],
  payload: ImagePartialUpdatePayload,
  messageId: string,
): AppliedImageUpdate | null {
  const rawPart = contentPartRecord(payload.content_part);
  const rawSlotId = typeof rawPart.slot_id === 'string' ? rawPart.slot_id : undefined;
  const rawSlotIndex = typeof rawPart.slot_index === 'number' ? rawPart.slot_index : undefined;
  const rawRevision = typeof rawPart.slot_revision === 'number'
    ? rawPart.slot_revision
    : undefined;
  if ((payload.slot_id !== undefined && payload.slot_id.length === 0)
    || (rawSlotId !== undefined && rawSlotId.length === 0)
    || (payload.slot_id && rawSlotId && payload.slot_id !== rawSlotId)
    || (payload.slot_index !== undefined && rawSlotIndex !== undefined
      && payload.slot_index !== rawSlotIndex)
    || (payload.slot_revision !== undefined && rawRevision !== undefined
      && payload.slot_revision !== rawRevision)) return null;

  const slotId = payload.slot_id ?? rawSlotId;
  const slotIndex = payload.slot_index ?? rawSlotIndex;
  const contentIndex = findRuntimeMediaSlotContentIndex(original, slotId, slotIndex);
  if (contentIndex < 0) return null;
  const current = original[contentIndex];
  if (!isRuntimeMediaImageSlot(current)) return null;

  const revision = payload.slot_revision ?? rawRevision;
  if (!Number.isInteger(revision) || revision === undefined || revision <= current.slot_revision) {
    return null;
  }
  if ((slotId && slotId !== current.slot_id) || (
    slotIndex !== undefined && slotIndex !== current.slot_index
  )) return null;

  const slotStatus = payload.error
    ? 'failed'
    : payload.slot_status ?? (
      typeof rawPart.slot_status === 'string'
        ? rawPart.slot_status as RuntimeMediaSlotStatus
        : undefined
    );
  if (!slotStatus) return null;
  if (current.slot_status === 'completed' && slotStatus === 'cancelled') return null;

  const candidate = buildRuntimeSlotCandidate(current, payload, rawPart, slotStatus);
  const parsed = parseContentPart({
    ...candidate,
    slot_id: current.slot_id,
    slot_index: current.slot_index,
    slot_status: slotStatus,
    slot_revision: revision,
  }, {
    messageId,
    source: 'ws:image_partial_update:runtime-slot',
  });
  if (!parsed || !isRuntimeMediaImageSlot(parsed)) return null;

  const content = [...original];
  content[contentIndex] = parsed;
  return { content, updatedPart: parsed };
}

function buildRuntimeSlotCandidate(
  current: ImagePart,
  payload: ImagePartialUpdatePayload,
  rawPart: Record<string, unknown>,
  slotStatus: RuntimeMediaSlotStatus,
): Record<string, unknown> {
  if (payload.error) {
    return {
      ...current,
      url: null,
      failed: true,
      error: payload.error,
      error_code: payload.error_code,
    };
  }
  if (payload.content_part) return rawPart;
  if (slotStatus === 'failed') return { ...current, failed: true };
  const clearsPreviousResult = slotStatus !== 'completed';
  const withoutPreviousError = { ...current };
  delete withoutPreviousError.error;
  delete withoutPreviousError.error_code;
  return {
    ...withoutPreviousError,
    ...(clearsPreviousResult ? { url: null } : {}),
    failed: false,
  };
}

function applyLegacyImageUpdate(
  original: ContentPart[],
  payload: ImagePartialUpdatePayload,
  messageId: string,
): AppliedImageUpdate | null {
  const imageIndex = payload.image_index ?? payload.slot_index;
  if (imageIndex === undefined || !Number.isInteger(imageIndex) || imageIndex < 0) return null;
  const content = [...original];
  let contentIndex = findImagePartContentIndex(content, imageIndex);
  while (contentIndex < 0) {
    content.push({ type: 'image', url: null });
    contentIndex = findImagePartContentIndex(content, imageIndex);
  }

  let parsed: ContentPart | null = null;
  if (payload.error) {
    parsed = {
      type: 'image', url: null, failed: true,
      error: payload.error, error_code: payload.error_code,
    };
  } else if (payload.content_part) {
    parsed = parseContentPart(payload.content_part, {
      messageId,
      source: 'ws:image_partial_update:legacy',
    });
  }
  if (!parsed || parsed.type !== 'image') return null;
  content[contentIndex] = parsed;
  return { content, updatedPart: parsed };
}
