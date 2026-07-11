/** WebSocket 任务完成、失败与图片 partial update 处理。 */

import { normalizeMessage, type Message } from '../stores/useMessageStore';
import { logger } from '../utils/logger';
import { tabSync } from '../utils/tabSync';
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

export function handleMessageDone(deps: HandlerDeps, msg: WSIncomingMessage): void {
  const { task_id, message_id, conversation_id } = msg;
  const messageData = (msg.message ?? msg.payload?.message) as Record<string, unknown> | undefined;

  if (deps.chunkBufferRef.current.size > 0) {
    if (deps.flushTimerRef.current) {
      clearTimeout(deps.flushTimerRef.current);
      deps.flushTimerRef.current = null;
    }
    flushChunkBuffer(deps);
  }

  logger.info('ws:message', 'done received', {
    taskId: task_id,
    messageId: message_id || messageData?.id,
    conversationId: conversation_id,
  });

  const store = deps.getStore();
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
      store.setStatus(message_id, 'completed');
      store.completeTask(task_id);
    }
    cleanupTaskSubscription(deps, task_id);
  } else if (messageData) {
    const normalized = normalizeMessage(messageData as NormalizeInput);
    const status = normalized.status === 'failed' ? 'failed' as const : 'completed' as const;
    store.updateMessage(message_id || (messageData.id as string), { ...normalized, status });
  } else if (message_id) {
    store.setStatus(message_id, 'completed');
  }

  if (effectiveConversationId && isNewlyCompleted) {
    store.completeStreaming(effectiveConversationId);
    store.markConversationCompleted(effectiveConversationId);
    store.setIsSending(false);
    tabSync.broadcast('message_completed', {
      conversationId: effectiveConversationId,
      messageId: message_id,
    });
  }

  if (isNewlyCompleted) {
    const isFailed = messageData && messageData.status === 'failed';
    import('react-hot-toast').then(({ default: toast }) => {
      if (isFailed) toast.error('生成失败');
      else toast.success('生成完成');
    });
  }

  const content = (messageData?.content ?? []) as Array<{ workspace_path?: string }>;
  if (Array.isArray(content) && content.some(part => part?.workspace_path)) {
    window.dispatchEvent(new CustomEvent('workspace:changed'));
  }
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
  if (message_id) {
    const errorText = error?.message || '生成失败';
    const existing = store.getMessage(message_id);
    const generationParams = existing?.generation_params;
    if (generationParams?.type === 'image') {
      const count = Math.max(1, Number(generationParams.num_images ?? 1));
      const current = existing?.content || [];
      const content = Array.from({ length: count }, (_, index) => {
        const part = current[index];
        if (part?.type === 'image' && part.url) return part;
        return {
          type: 'image' as const,
          url: null,
          failed: true,
          error: errorText,
          error_code: error?.code,
        };
      });
      store.updateMessage(message_id, {
        status: 'failed',
        is_error: false,
        error: { code: error?.code ?? 'UNKNOWN', message: errorText },
        content,
      });
    } else {
      store.updateMessage(message_id, {
        status: 'failed',
        is_error: true,
        error: { code: error?.code ?? 'UNKNOWN', message: errorText },
        content: [{ type: 'text', text: errorText }],
      });
    }
  }

  if (task_id) handleTaskFailure(deps, task_id, error);
  if (conversation_id) store.completeStreaming(conversation_id);
  store.setIsSending(false);
  import('react-hot-toast').then(({ default: toast }) => {
    toast.error(error?.message || '生成失败');
  });
}

export function handleImagePartialUpdate(
  deps: HandlerDeps,
  msg: WSIncomingMessage,
): void {
  const { message_id } = msg;
  const payload = (msg.payload || {}) as {
    image_index?: number;
    content_part?: Message['content'][number];
    completed_count?: number;
    total_count?: number;
    error?: string;
    error_code?: string;
  };
  const { image_index, content_part, completed_count, total_count, error, error_code } = payload;
  if (!message_id || image_index === undefined) return;

  logger.info('ws:image', 'partial update', {
    messageId: message_id,
    imageIndex: image_index,
    progress: `${completed_count}/${total_count}`,
    hasError: !!error,
  });

  const store = deps.getStore();
  const existing = store.getMessage(message_id);
  if (!existing) return;
  const content = [...(existing.content || [])];
  while (content.length <= image_index) {
    content.push({ type: 'image', url: null } as Message['content'][number]);
  }

  if (error) {
    content[image_index] = {
      type: 'image', url: null, failed: true, error, error_code,
    } as Message['content'][number];
  } else if (content_part) {
    content[image_index] = content_part;
  }
  store.updateMessage(message_id, { content });

  if (content_part && 'workspace_path' in content_part && content_part.workspace_path) {
    window.dispatchEvent(new CustomEvent('workspace:changed'));
  }
}
