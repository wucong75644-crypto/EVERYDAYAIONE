/** WebSocket 消息处理器的共享类型与基础操作。 */

import type { OperationContext } from './WebSocketContext';
import type { WSMessage } from '../hooks/useWebSocket';
import type { ContentPart, MessageStatus, TextPart, ToolStepPart } from '../types/message';
import type { Message } from '../stores/useMessageStore';

export interface WSIncomingMessage extends WSMessage {
  message_id?: string;
  message?: unknown;
  chunk?: unknown;
  accumulated?: string;
  error?: { code?: string; message?: string };
  credits?: number;
  progress?: number;
  data?: Record<string, unknown>;
}

export interface DeliveryCursor {
  streamId: string;
  executionAttempt: number;
  lastSeq: number;
  snapshotApplied: boolean;
}

export interface MessageStoreActions {
  setStatus: (messageId: string, status: MessageStatus) => void;
  appendStreamingContent: (conversationId: string, chunk: string) => void;
  appendContent: (messageId: string, chunk: string) => void;
  updateTaskProgress: (taskId: string, progress: number) => void;
  updateMessage: (messageId: string, data: Partial<Message>) => void;
  addMessage: (conversationId: string, message: Message) => void;
  completeTask: (taskId: string) => void;
  failTask: (taskId: string, error: string) => void;
  completeStreaming: (conversationId: string) => void;
  completeStreamingWithMessage: (conversationId: string, message: Message) => void;
  getStreamingMessageId: (conversationId: string) => string | null;
  registerStreamingId: (conversationId: string, messageId: string) => void;
  markConversationCompleted: (conversationId: string) => void;
  setIsSending: (isSending: boolean) => void;
  getMessage: (messageId: string) => Message | undefined;
  setStreamingContent: (conversationId: string, content: string) => void;
  restoreStreamingBlocks: (conversationId: string, blocks: ContentPart[], remainingText: string) => void;
  replaceLastTextBlock: (conversationId: string, block: TextPart) => void;
  setAgentStepHint: (conversationId: string, hint: string) => void;
  clearAgentStepHint: (conversationId: string) => void;
  appendStreamingThinking: (conversationId: string, chunk: string) => void;
  appendContentBlock: (conversationId: string, block: ContentPart) => void;
  updateContentBlock: (conversationId: string, toolCallId: string, updates: Partial<ToolStepPart>) => void;
  markForceRefresh: (conversationId: string) => void;
  setSuggestions: (conversationId: string, suggestions: string[]) => void;
  setToolConfirmRequest: (request: {
    toolCallId: string;
    taskId: string;
    conversationId: string;
    toolName: string;
    arguments: Record<string, unknown>;
    description: string;
    timeout: number;
  } | null) => void;
}

export interface HandlerDeps {
  getStore: () => MessageStoreActions;
  subscribedTasksRef: React.RefObject<Set<string>>;
  taskConversationMapRef: React.RefObject<Map<string, string>>;
  operationContextRef: React.RefObject<Map<string, OperationContext>>;
  chunkBufferRef: React.RefObject<Map<string, { chunk: string; conversationId: string }>>;
  flushTimerRef: React.RefObject<ReturnType<typeof setTimeout> | null>;
  unsubscribeTask: (taskId: string) => void;
  send: (message: Omit<WSMessage, 'timestamp'>) => void;
  deliveryCursorRef?: React.RefObject<Map<string, DeliveryCursor>>;
}

export function acceptDeliveryEvent(
  deps: HandlerDeps,
  msg: WSIncomingMessage,
): boolean {
  const taskId = msg.task_id;
  const payload = msg.payload || {};
  const streamId = typeof payload.stream_id === 'string' ? payload.stream_id : undefined;
  const attempt = typeof payload.execution_attempt === 'number'
    ? payload.execution_attempt
    : undefined;
  const seq = typeof payload.delivery_seq === 'number' ? payload.delivery_seq : undefined;
  if (!taskId || !streamId || attempt === undefined || seq === undefined) return true;
  const cursors = deps.deliveryCursorRef?.current;
  if (!cursors) return true;

  const current = cursors.get(taskId);
  if (current && attempt < current.executionAttempt) return false;
  if (
    current
    && streamId === current.streamId
    && attempt === current.executionAttempt
    && seq <= current.lastSeq
  ) {
    return false;
  }
  if (
    current
    && streamId === current.streamId
    && attempt === current.executionAttempt
    && seq > current.lastSeq + 1
  ) {
    deps.send({
      type: 'subscribe',
      payload: {
        task_id: taskId,
        last_index: current.lastSeq,
        last_delivery_seq: current.lastSeq,
      },
    });
    return false;
  }
  if (!current || attempt > current.executionAttempt || streamId !== current.streamId) {
    cursors.set(taskId, {
      streamId, executionAttempt: attempt, lastSeq: seq, snapshotApplied: false,
    });
    return true;
  }
  cursors.set(taskId, { ...current, lastSeq: seq });
  return true;
}

export function setDeliveryCursor(
  deps: HandlerDeps,
  taskId: string,
  streamId: string | undefined,
  executionAttempt: number | undefined,
  seq: number | undefined,
  snapshotApplied = false,
): void {
  if (!streamId || executionAttempt === undefined || seq === undefined) return;
  const cursors = deps.deliveryCursorRef?.current;
  if (!cursors) return;
  const current = cursors.get(taskId);
  if (
    current
    && current.streamId === streamId
    && current.executionAttempt === executionAttempt
  ) {
    cursors.set(taskId, {
      ...current,
      lastSeq: Math.max(current.lastSeq, seq),
      snapshotApplied: current.snapshotApplied || snapshotApplied,
    });
    return;
  }
  if (!current || executionAttempt >= current.executionAttempt) {
    cursors.set(taskId, {
      streamId, executionAttempt, lastSeq: seq, snapshotApplied,
    });
  }
}

export function cleanupTaskSubscription(deps: HandlerDeps, taskId: string): void {
  deps.subscribedTasksRef.current.delete(taskId);
  deps.taskConversationMapRef.current.delete(taskId);
  deps.unsubscribeTask(taskId);
  deps.deliveryCursorRef?.current.delete(taskId);
}

export function flushChunkBuffer(deps: HandlerDeps): void {
  const buffer = deps.chunkBufferRef.current;
  if (buffer.size === 0) return;

  const store = deps.getStore();
  buffer.forEach((data, messageId) => {
    if (data.conversationId) {
      store.appendStreamingContent(data.conversationId, data.chunk);
    } else {
      store.appendContent(messageId, data.chunk);
    }
  });
  buffer.clear();
  deps.flushTimerRef.current = null;
}
