/**
 * 消息流投影层。
 *
 * WebSocket 事件可能来自实时连接、重连回放或 Redis 转发，不能假设它们
 * 一定先于本地占位消息到达。这里统一解决三件事：
 * 1. 从 task/message/payload 恢复消息身份；
 * 2. 在投影任何流事件前建立 streaming message 绑定；
 * 3. 对带 delivery_seq 的事件做单调去重，避免实时事件和回放重复落 Store。
 *
 * 这层只负责“事件如何进入前端 Store”，不改变后端 Runtime 的所有权、
 * lease 或任务状态语义。
 */

import type { ContentPart, TextPart } from '../types/message';
import { parseContentPart } from '../schemas/messageProtocol';
import type {
  HandlerDeps,
  MessageStoreActions,
  WSIncomingMessage,
} from './wsMessageHandlerShared';

type DeliveryMetadata = {
  streamId?: string;
  executionAttempt?: number;
  deliverySeq?: number;
};

type ReplayEvent = {
  event_type?: unknown;
  payload?: unknown;
  delivery_seq?: unknown;
};

function stringValue(value: unknown): string | undefined {
  return typeof value === 'string' && value.length > 0 ? value : undefined;
}

function numberValue(value: unknown): number | undefined {
  return typeof value === 'number' && Number.isInteger(value) ? value : undefined;
}

function metadata(msg: WSIncomingMessage): DeliveryMetadata {
  const payload = msg.payload || {};
  return {
    streamId: stringValue(msg.stream_id) ?? stringValue(payload.stream_id),
    executionAttempt: numberValue(msg.execution_attempt)
      ?? numberValue(payload.execution_attempt),
    deliverySeq: numberValue(msg.delivery_seq) ?? numberValue(payload.delivery_seq),
  };
}

function payloadString(payload: Record<string, unknown>, key: string): string | undefined {
  return stringValue(payload[key]);
}

export interface MessageStreamProjection {
  resolveConversationId: (msg: WSIncomingMessage) => string | undefined;
  resolveMessageId: (msg: WSIncomingMessage) => string | undefined;
  acceptDelivery: (msg: WSIncomingMessage) => boolean;
  setDeliveryCursor: (
    taskId: string,
    streamId: string | undefined,
    executionAttempt: number | undefined,
    seq: number | undefined,
    snapshotApplied?: boolean,
  ) => void;
  ensureBinding: (conversationId: string, messageId: string) => void;
  ensureMessageBinding: (msg: WSIncomingMessage, conversationId?: string) => string | undefined;
  appendTextChunk: (conversationId: string, chunk: string) => void;
  appendThinkingChunk: (conversationId: string, chunk: string) => void;
  restore: (
    conversationId: string,
    messageId: string | undefined,
    blocks: ContentPart[],
    remainingText: string,
  ) => void;
  projectBlock: (conversationId: string, block: ContentPart) => void;
  projectReplayEvent: (
    conversationId: string,
    messageId: string | undefined,
    taskId: string,
    streamId: string | undefined,
    executionAttempt: number | undefined,
    event: ReplayEvent,
  ) => void;
}

export function createMessageStreamProjection(deps: HandlerDeps): MessageStreamProjection {
  const lastDeliveryByStream = new Map<string, number>();
  const getStore = (): MessageStoreActions => deps.getStore();

  const resolveConversationId = (msg: WSIncomingMessage): string | undefined => {
    const payload = msg.payload || {};
    const payloadTaskId = payloadString(payload, 'task_id');
    return stringValue(msg.conversation_id)
      || payloadString(payload, 'conversation_id')
      || (msg.task_id ? deps.taskConversationMapRef.current.get(msg.task_id) : undefined)
      || (payloadTaskId ? deps.taskConversationMapRef.current.get(payloadTaskId) : undefined);
  };

  const resolveMessageId = (msg: WSIncomingMessage): string | undefined => {
    const payload = msg.payload || {};
    const nestedMessage = payload.message;
    return stringValue(msg.message_id)
      || payloadString(payload, 'message_id')
      || (nestedMessage && typeof nestedMessage === 'object' && !Array.isArray(nestedMessage)
        ? stringValue((nestedMessage as Record<string, unknown>).id)
        : undefined);
  };

  const acceptDelivery = (msg: WSIncomingMessage): boolean => {
    const info = metadata(msg);
    if (info.deliverySeq === undefined) return true;

    const taskId = msg.task_id || payloadString(msg.payload || {}, 'task_id');
    if (taskId && info.streamId && info.executionAttempt !== undefined
      && deps.deliveryCursorRef?.current) {
      const cursors = deps.deliveryCursorRef.current;
      const current = cursors.get(taskId);
      if (current && info.executionAttempt < current.executionAttempt) return false;
      if (current
        && current.streamId === info.streamId
        && current.executionAttempt === info.executionAttempt
        && info.deliverySeq <= current.lastSeq) return false;
      if (current
        && current.streamId === info.streamId
        && current.executionAttempt === info.executionAttempt
        && info.deliverySeq > current.lastSeq + 1) {
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
      cursors.set(taskId, {
        streamId: info.streamId,
        executionAttempt: info.executionAttempt,
        lastSeq: info.deliverySeq,
        snapshotApplied: current?.snapshotApplied ?? false,
      });
      return true;
    }

    const streamKey = info.streamId
      || resolveMessageId(msg)
      || msg.task_id
      || `${msg.type}:${resolveConversationId(msg) || 'unknown'}`;
    const attemptKey = info.executionAttempt === undefined ? '' : `:${info.executionAttempt}`;
    const key = `${streamKey}${attemptKey}`;
    const previous = lastDeliveryByStream.get(key);
    if (previous !== undefined && info.deliverySeq <= previous) return false;
    lastDeliveryByStream.set(key, info.deliverySeq);
    return true;
  };

  const setDeliveryCursor = (
    taskId: string,
    streamId: string | undefined,
    executionAttempt: number | undefined,
    seq: number | undefined,
    snapshotApplied = false,
  ): void => {
    if (!streamId || executionAttempt === undefined || seq === undefined) return;
    const cursors = deps.deliveryCursorRef?.current;
    if (cursors) {
      const current = cursors.get(taskId);
      if (current
        && current.streamId === streamId
        && current.executionAttempt === executionAttempt) {
        cursors.set(taskId, {
          ...current,
          lastSeq: Math.max(current.lastSeq, seq),
          snapshotApplied: current.snapshotApplied || snapshotApplied,
        });
      } else if (!current || executionAttempt >= current.executionAttempt) {
        cursors.set(taskId, {
          streamId, executionAttempt, lastSeq: seq, snapshotApplied,
        });
      }
      return;
    }
    const key = `${streamId}:${executionAttempt}`;
    const previous = lastDeliveryByStream.get(key);
    if (previous === undefined || seq > previous) lastDeliveryByStream.set(key, seq);
  };

  const ensureBinding = (conversationId: string, messageId: string): void => {
    const store = getStore();
    if (store.getStreamingMessageId(conversationId) === messageId) return;
    // registerStreamingId 是现有 Store 的生命周期入口；它现在也负责在
    // 回放先于本地占位符到达时创建最小 assistant streaming 占位符。
    store.registerStreamingId?.(conversationId, messageId);
  };

  const ensureMessageBinding = (
    msg: WSIncomingMessage,
    conversationId = resolveConversationId(msg),
  ): string | undefined => {
    const messageId = resolveMessageId(msg);
    if (conversationId && messageId) ensureBinding(conversationId, messageId);
    return conversationId;
  };

  const appendTextChunk = (conversationId: string, chunk: string): void => {
    if (chunk) getStore().appendStreamingContent(conversationId, chunk);
  };

  const appendThinkingChunk = (conversationId: string, chunk: string): void => {
    if (chunk) getStore().appendStreamingThinking(conversationId, chunk);
  };

  const restore = (
    conversationId: string,
    messageId: string | undefined,
    blocks: ContentPart[],
    remainingText: string,
  ): void => {
    if (messageId) ensureBinding(conversationId, messageId);
    if (blocks.length > 0) {
      getStore().restoreStreamingBlocks(conversationId, blocks, remainingText);
    } else if (remainingText) {
      getStore().setStreamingContent(conversationId, remainingText);
    }
  };

  const projectBlock = (conversationId: string, block: ContentPart): void => {
    const store = getStore();
    if (block.type === 'tool_step' && block.tool_call_id && block.status !== 'running') {
      store.updateContentBlock(conversationId, block.tool_call_id, block);
      return;
    }
    if (block.type === 'text') {
      store.replaceLastTextBlock(conversationId, block as TextPart);
      return;
    }
    store.appendContentBlock(conversationId, block);
  };

  const projectReplayEvent = (
    conversationId: string,
    messageId: string | undefined,
    taskId: string,
    streamId: string | undefined,
    executionAttempt: number | undefined,
    event: ReplayEvent,
  ): void => {
    const eventType = stringValue(event.event_type);
    const eventPayload = event.payload && typeof event.payload === 'object'
      && !Array.isArray(event.payload)
      ? event.payload as Record<string, unknown>
      : null;
    const seq = numberValue(event.delivery_seq);
    const replayMessage: WSIncomingMessage = {
      type: (eventType || 'message_chunk') as WSIncomingMessage['type'],
      task_id: taskId,
      message_id: messageId,
      payload: {
        ...(eventPayload || {}),
        ...(streamId ? { stream_id: streamId } : {}),
        ...(executionAttempt !== undefined ? { execution_attempt: executionAttempt } : {}),
        ...(seq !== undefined ? { delivery_seq: seq } : {}),
      },
      timestamp: Date.now(),
    };
    if (!acceptDelivery(replayMessage)) return;
    const chunk = stringValue(eventPayload?.chunk);
    if (eventType === 'message_chunk' && chunk) appendTextChunk(conversationId, chunk);
    if (eventType === 'thinking_chunk' && chunk) appendThinkingChunk(conversationId, chunk);
    if (eventType === 'content_block_add' && eventPayload?.block) {
      const block = parseContentPart(eventPayload.block, {
        messageId,
        conversationId,
        source: 'ws:delivery_replay',
      });
      if (block) projectBlock(conversationId, block);
    }
  };

  return {
    resolveConversationId,
    resolveMessageId,
    acceptDelivery,
    setDeliveryCursor,
    ensureBinding,
    ensureMessageBinding,
    appendTextChunk,
    appendThinkingChunk,
    restore,
    projectBlock,
    projectReplayEvent,
  };
}
