/** WebSocket 消息处理器的共享类型与基础操作。 */

import type { OperationContext } from './WebSocketContext';
import type { WSMessage } from '../hooks/useWebSocket';
import type { MessageStatus } from '../types/message';
import type { Message } from '../stores/useMessageStore';

export interface WSIncomingMessage extends WSMessage {
  message_id?: string;
  message?: unknown;
  chunk?: string;
  accumulated?: string;
  error?: { code?: string; message?: string };
  credits?: number;
  progress?: number;
  data?: Record<string, unknown>;
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
  markConversationCompleted: (conversationId: string) => void;
  setIsSending: (isSending: boolean) => void;
  getMessage: (messageId: string) => Message | undefined;
  setStreamingContent: (conversationId: string, content: string) => void;
  restoreStreamingBlocks: (conversationId: string, blocks: Array<Record<string, unknown>>, remainingText: string) => void;
  replaceLastTextBlock: (conversationId: string, block: { type: 'text'; text: string }) => void;
  setAgentStepHint: (conversationId: string, hint: string) => void;
  clearAgentStepHint: (conversationId: string) => void;
  appendStreamingThinking: (conversationId: string, chunk: string) => void;
  appendContentBlock: (conversationId: string, block: Record<string, unknown>) => void;
  updateContentBlock: (conversationId: string, toolCallId: string, updates: Record<string, unknown>) => void;
  markForceRefresh: (conversationId: string) => void;
  setSuggestions: (conversationId: string, suggestions: string[]) => void;
  setToolConfirmRequest: (request: {
    toolCallId: string;
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
}

export function cleanupTaskSubscription(deps: HandlerDeps, taskId: string): void {
  deps.subscribedTasksRef.current.delete(taskId);
  deps.taskConversationMapRef.current.delete(taskId);
  deps.unsubscribeTask(taskId);
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
