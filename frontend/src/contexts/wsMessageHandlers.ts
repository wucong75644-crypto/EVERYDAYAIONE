/**
 * WebSocket 消息处理器工厂
 *
 * 从 WebSocketContext.tsx 提取的纯函数逻辑，包括：
 * - 10 种 WS 消息类型的处理器
 * - chunk 缓冲 flush 机制
 * - 任务完成/失败辅助函数
 */

import { useAuthStore } from '../stores/useAuthStore';
import { useMemoryStore } from '../stores/useMemoryStore';
import { logger } from '../utils/logger';
import { calcRemainingText } from '../utils/messageUtils';
import { parseContentPart, parseContentParts, parseProtocolString } from '../schemas/messageProtocol';
import type { WSMessage } from '../hooks/useWebSocket';
import type { TaskStatus } from '../types/scheduledTask';
import { getAgentStepText, getToolCallText } from '../constants/placeholder';
import { handleRoutingComplete } from './wsRoutingCompleteHandler';
import {
  flushChunkBuffer,
  type HandlerDeps,
  type WSIncomingMessage,
} from './wsMessageHandlerShared';
import { createMessageStreamProjection, type MessageStreamProjection } from './messageStreamProjection';
import {
  handleImagePartialUpdate,
  handleMessageDone,
  handleMessageError,
  handleControlResult,
} from './wsTaskMessageHandlers';

export {
  flushChunkBuffer,
  type HandlerDeps,
  type MessageStoreActions,
} from './wsMessageHandlerShared';

/**
 * WS 消息扩展类型 — 后端各消息类型可能携带的额外字段
 * 仅处理器内部使用，外部统一使用 WSMessage
 */

type HandlerDefinition = (
  deps: HandlerDeps,
  msg: WSIncomingMessage,
  projection: MessageStreamProjection,
) => void;

const handlerDefinitions: Record<string, HandlerDefinition> = {
  message_start: (deps, msg, projection) => {
      const { message_id } = msg;
      if (!message_id && !projection.resolveMessageId(msg)) return;

      if (!projection.acceptDelivery(msg)) return;
      const effectiveMessageId = projection.resolveMessageId(msg);
      const conversationId = projection.resolveConversationId(msg);
      if (conversationId && effectiveMessageId) {
        deps.getStore().registerStreamingId(conversationId, effectiveMessageId);
      }

      logger.info('ws:message', 'start received', { messageId: effectiveMessageId });
      if (effectiveMessageId) deps.getStore().setStatus(effectiveMessageId, 'streaming');
    },

  message_chunk: (deps, msg, projection) => {
      const { task_id } = msg;
      const message_id = projection.resolveMessageId(msg);
      const conversation_id = projection.ensureMessageBinding(msg);
      const chunk = parseProtocolString(msg.chunk ?? msg.payload?.chunk, 'chunk', {
        messageId: message_id,
        conversationId: conversation_id,
        source: 'ws:message_chunk',
      });
      if (!message_id || !chunk) return;
      if (!projection.acceptDelivery(msg)) return;

      // 没有关联对话时保留历史的按 message_id 投影路径。
      if (!conversation_id) {
        deps.getStore().appendContent(message_id, chunk);
        return;
      }

      const bufferData = deps.chunkBufferRef.current.get(message_id);
      const prevChunk = bufferData?.chunk || '';
      const accumulated = prevChunk + chunk;

      deps.chunkBufferRef.current.set(message_id, {
        chunk: accumulated,
        conversationId: conversation_id,
      });

      if (task_id) {
        const context = deps.operationContextRef.current.get(task_id);
        if (context?.onStreamChunk) {
          context.onStreamChunk(chunk, accumulated);
        }
      }

      // 首字节立即渲染，后续 chunk 用 16ms（约1帧）批量窗口
      const isFirstChunk = !bufferData;
      if (isFirstChunk) {
        if (deps.flushTimerRef.current) {
          clearTimeout(deps.flushTimerRef.current);
          deps.flushTimerRef.current = null;
        }
        flushChunkBuffer(deps);
        // flush 后 buffer 已清空，重新标记该消息（防止后续 chunk 被当成首字节）
        deps.chunkBufferRef.current.set(message_id, { chunk: '', conversationId: conversation_id });
      } else if (!deps.flushTimerRef.current) {
        deps.flushTimerRef.current = setTimeout(() => flushChunkBuffer(deps), 16);
      }
    },

    // stream_end：LLM 流结束信号（对标 Anthropic message_stop）。
    // 它只代表模型网络流结束，不代表数据库 commit 已成功；最终状态由
    // message_done/message_error 或暂停/取消恢复事件确认。
  stream_end: (deps, msg, projection) => {
      const { message_id, conversation_id } = msg;
      if (!projection.acceptDelivery(msg)) return;
      logger.info('ws:message', 'stream_end received', { messageId: message_id, conversationId: conversation_id });

      // flush 残留 chunk
      if (deps.chunkBufferRef.current.size > 0) {
        if (deps.flushTimerRef.current) {
          clearTimeout(deps.flushTimerRef.current);
          deps.flushTimerRef.current = null;
        }
        flushChunkBuffer(deps);
      }

      // Actor 暂停已在 PostgreSQL 中物化 interrupted 快照。沿用 stream_end
      // 协议把该快照投影到每个已连接页面，避免多标签只能等重连才收敛。
      const deliveryStatus = msg.payload?.delivery_status;
      const pausedMessage = msg.payload?.message;
      const effectiveConversationId = projection.resolveConversationId(msg);
      if (
        deliveryStatus === 'paused'
        && effectiveConversationId
        && pausedMessage
        && typeof pausedMessage === 'object'
        && !Array.isArray(pausedMessage)
      ) {
        deps.getStore().completeStreamingWithMessage(
          effectiveConversationId,
          pausedMessage as Parameters<MessageStoreActions['completeStreamingWithMessage']>[1],
        );
      }

      // Agent 操作完成 → 通知工作区刷新（覆盖删除等无 file block 的场景）
      window.dispatchEvent(new CustomEvent('workspace:changed'));
    },

  message_progress: (deps, msg) => {
      const { task_id } = msg;
      const progress = msg.progress ?? (msg.payload?.progress as number | undefined);
      if (!task_id || progress === undefined) return;

      logger.debug('ws:message', 'progress update', { taskId: task_id, progress });
      deps.getStore().updateTaskProgress(task_id, progress);
    },

  message_done: (deps, msg) => handleMessageDone(deps, msg),

  message_error: (deps, msg) => handleMessageError(deps, msg),

  control_result: (deps, msg) => handleControlResult(deps, msg),

  image_partial_update: (deps, msg) => handleImagePartialUpdate(deps, msg),

  credits_changed: (_deps, msg) => {
      const credits = msg.credits ?? (msg.payload?.credits as number | undefined);
      if (credits === undefined) return;

      logger.info('ws:credits', 'credits changed', { credits });

      const currentUser = useAuthStore.getState().user;
      if (currentUser) {
        useAuthStore.getState().setUser({ ...currentUser, credits });
      }
    },

  subscribed: (deps, msg, projection) => {
      const payload = msg.payload || {};
      const task_id = typeof payload.task_id === 'string' ? payload.task_id : msg.task_id;
      if (!projection.acceptDelivery(msg)) return;
      const streamId = typeof payload.stream_id === 'string' ? payload.stream_id : undefined;
      const executionAttempt = typeof payload.execution_attempt === 'number'
        ? payload.execution_attempt : undefined;
      const snapshotSeq = typeof payload.snapshot_seq === 'number'
        ? payload.snapshot_seq : undefined;
      const accumulated = parseProtocolString(payload.accumulated, 'accumulated', {
        source: 'ws:subscribed',
      });
      const accumulatedBlocks = payload.accumulated_blocks === undefined
        ? []
        : parseContentParts(payload.accumulated_blocks, { source: 'ws:subscribed' });

      logger.info('ws:subscribe', 'confirmed', {
        taskId: task_id,
        accumulatedLen: accumulated?.length ?? 0,
        blocksCount: accumulatedBlocks.length,
      });

      if (task_id) {
        const conversationId = projection.resolveConversationId({ ...msg, task_id });
        const messageId = projection.resolveMessageId(msg);
        if (conversationId) {
          const deliveryStatus = typeof payload.delivery_status === 'string'
            ? payload.delivery_status : undefined;
          const isTerminalDelivery = deliveryStatus === 'paused'
            || deliveryStatus === 'committed'
            || deliveryStatus === 'failed'
            || deliveryStatus === 'cancelled';
          // Older subscription snapshots may not carry message_id.  They still
          // need their accumulated state restored; only active snapshots with
          // an explicit message id should establish a streaming binding.
          const shouldBind = Boolean(messageId) && !isTerminalDelivery;
          if (deliveryStatus === 'paused') {
            // 订阅快照也可能是唯一可见事件（例如另一个标签刚暂停）。
            // 先停止本地流状态，持久化消息随后由任务对账回读完整 marker/内容。
            if (messageId) deps.getStore().updateMessage(messageId, { status: 'interrupted' });
            deps.getStore().completeStreaming(conversationId);
          }
          if (shouldBind) {
            projection.ensureBinding(conversationId, messageId);
            deps.getStore().registerStreamingId(conversationId, messageId);
          }

          const currentCursor = deps.deliveryCursorRef?.current.get(task_id);
          const shouldRestore = !currentCursor
            || currentCursor.streamId !== streamId
            || !currentCursor.snapshotApplied
            || snapshotSeq === undefined
            || currentCursor.lastSeq <= snapshotSeq;
          if (shouldRestore && !isTerminalDelivery) {
            if (accumulatedBlocks.length > 0) {
              const remaining = calcRemainingText(accumulatedBlocks, accumulated);
              projection.restore(conversationId, messageId, accumulatedBlocks, remaining);
            } else if (accumulated && accumulated.length > 0) {
              // 向后兼容：无 blocks 时仅恢复纯文字
              projection.restore(conversationId, messageId, [], accumulated);
            }
          }

          if (task_id && streamId) {
            projection.setDeliveryCursor(
              task_id, streamId, executionAttempt, snapshotSeq ?? 0, true,
            );
          }

          const events = Array.isArray(payload.events) ? payload.events : [];
          for (const event of events) {
            if (!event || typeof event !== 'object' || Array.isArray(event)) continue;
            projection.projectReplayEvent(
              conversationId,
              messageId,
              task_id,
              streamId,
              executionAttempt,
              event as { event_type?: unknown; payload?: unknown; delivery_seq?: unknown },
            );
          }
        }
      }
    },

  memory_extracted: (_deps, msg) => {
      const data = (msg.data ?? msg.payload) as { memories?: unknown[]; count?: number };
      if (!data?.memories) return;

      logger.info('ws:memory', 'memories extracted', { count: data.count });

      useMemoryStore.getState().onMemoryExtracted(
        data.memories as Array<{ id: string; memory: string }>,
      );
    },

  thinking_chunk: (_deps, msg, projection) => {
      const conversation_id = projection.ensureMessageBinding(msg);
      const chunk = parseProtocolString(msg.chunk ?? msg.payload?.chunk, 'chunk', {
        conversationId: conversation_id,
        source: 'ws:thinking_chunk',
      });
      if (!conversation_id || !chunk) return;
      if (!projection.acceptDelivery(msg)) return;

      projection.appendThinkingChunk(conversation_id, chunk);
    },

  agent_step: (deps, msg) => {
      const { conversation_id } = msg;
      const toolName = msg.payload?.tool_name as string | undefined;
      if (!conversation_id || !toolName) return;

      const hint = getAgentStepText(toolName);
      deps.getStore().setAgentStepHint(conversation_id, hint);
    },

  routing_complete: handleRoutingComplete,

  conversation_updated: (deps, msg) => {
      const { conversation_id } = msg;
      if (!conversation_id) return;

      logger.info('ws:conversation', 'conversation updated (wecom)', { conversationId: conversation_id });

      // 通知 ConversationList 刷新列表
      if (typeof window !== 'undefined') {
        window.dispatchEvent(
          new CustomEvent('conversation-list-refresh', {
            detail: { conversationId: conversation_id },
          }),
        );
      }

      // 标记该对话消息需要强制刷新（用户切入时重新加载）
      const store = deps.getStore();
      store.markForceRefresh(conversation_id);
    },

  tool_call: (deps, msg) => {
      const { conversation_id } = msg;
      const toolCalls = msg.payload?.tool_calls as Array<{ name: string }> | undefined;
      const turn = msg.payload?.turn as number | undefined;
      if (!conversation_id || !toolCalls?.length) return;

      // 取第一个工具名展示提示（多工具时显示第一个）
      const hint = getToolCallText(toolCalls[0].name);
      const suffix = toolCalls.length > 1 ? ` 等${toolCalls.length}个工具` : '';
      deps.getStore().setAgentStepHint(conversation_id, `${hint}${suffix}`);

      logger.info('ws:tool', 'tool_call', { conversationId: conversation_id, tools: toolCalls.map(t => t.name), turn });
    },

  tool_result: (deps, msg) => {
      const { conversation_id } = msg;
      const toolName = msg.payload?.tool_name as string | undefined;
      const success = msg.payload?.success as boolean | undefined;
      if (!conversation_id) return;

      // 工具完成后清除提示（下一轮 stream 开始时会自动更新）
      deps.getStore().clearAgentStepHint(conversation_id);

      logger.info('ws:tool', 'tool_result', { conversationId: conversation_id, tool: toolName, success });
    },

  content_block_add: (_deps, msg, projection) => {
      const conversation_id = projection.ensureMessageBinding(msg);
      const block = parseContentPart(msg.payload?.block, {
        messageId: msg.message_id,
        conversationId: conversation_id,
        source: 'ws:content_block_add',
      });
      if (!conversation_id || !block) return;
      if (!projection.acceptDelivery(msg)) return;

      // 统一投影：工具 running/terminal、文本完整块和其他结构化 block
      // 都经过同一入口，避免某种事件先到时被 Store 静默丢弃。
      projection.projectBlock(conversation_id, block);
      if (block.type === 'tool_step' && block.tool_call_id && block.status !== 'running') {
        logger.info('ws:content', 'tool_step_update', {
          conversationId: conversation_id,
          toolCallId: block.tool_call_id,
          status: block.status,
        });
        return;
      }

      if (block.type === 'text') {
        logger.info('ws:content', 'text_block_replace', { conversationId: conversation_id });
        return;
      }

      logger.info('ws:content', 'content_block_add', { conversationId: conversation_id, type: block.type });

      // 文件产出 → 通知工作区刷新
      if (block.type === 'file') {
        window.dispatchEvent(new CustomEvent('workspace:changed'));
      }
    },

  suggestions_ready: (deps, msg) => {
      const { conversation_id } = msg;
      const suggestions = msg.payload?.suggestions as string[] | undefined;
      if (!conversation_id || !suggestions?.length) return;

      deps.getStore().setSuggestions(conversation_id, suggestions);
      logger.info('ws:suggestions', 'suggestions_ready', { conversationId: conversation_id, count: suggestions.length });
    },

  tool_confirm_request: (deps, msg) => {
      const { conversation_id, task_id } = msg;
      const toolCallId = msg.payload?.tool_call_id as string | undefined;
      const toolName = msg.payload?.tool_name as string | undefined;
      const description = msg.payload?.description as string | undefined;
      const args = (msg.payload?.arguments ?? {}) as Record<string, unknown>;
      const timeout = (msg.payload?.timeout as number) || 60;
      if (!conversation_id || !task_id || !toolCallId || !toolName) {
        logger.warn('ws:tool', 'confirm_request_missing_scope', {
          taskId: task_id,
          conversationId: conversation_id,
          toolCallId,
        });
        return;
      }

      // 显示步骤提示
      deps.getStore().setAgentStepHint(conversation_id, `⚠ ${description || toolName} — 等待确认`);

      // 触发确认弹窗
      deps.getStore().setToolConfirmRequest({
        toolCallId,
        taskId: task_id,
        conversationId: conversation_id,
        toolName,
        arguments: args,
        description: description || `AI 要执行: ${toolName}`,
        timeout,
      });

      logger.info('ws:tool', 'confirm_request', { conversationId: conversation_id, tool: toolName, taskId: task_id });
    },

  error: (_deps, msg) => {
      const message = msg.message ?? msg.payload?.message;
      logger.error('ws:error', 'error received', undefined, { error: message });
    },

    // ── 定时任务事件 ──
  scheduled_task_started: (_deps, msg) => {
      const data = (msg.data || msg.payload) as { task_id?: string; task_name?: string };
      if (!data?.task_id) return;
      logger.info('ws:scheduled-task', 'started', data);
      // 异步导入，避免循环依赖
      import('../stores/useScheduledTaskStore').then(({ useScheduledTaskStore }) => {
        useScheduledTaskStore.getState().optimisticUpdate(data.task_id!, {
          status: 'running',
        });
      });
    },

  scheduled_task_completed: (_deps, msg) => {
      const data = (msg.data || msg.payload) as {
        task_id?: string;
        run_id?: string;
        task_status?: string;
        status?: string;
        next_run_at?: string;
        summary?: string;
      };
      if (!data?.task_id) return;
      const validStatuses: TaskStatus[] = ['active', 'paused', 'error', 'running'];
      const taskStatus = data.task_status !== undefined
        ? (validStatuses.includes(data.task_status as TaskStatus)
          ? data.task_status as TaskStatus : undefined)
        : (data.status === 'success' ? 'active' : undefined);
      logger.info('ws:scheduled-task', 'completed', data);
      import('../stores/useScheduledTaskStore').then(({ useScheduledTaskStore }) => {
        useScheduledTaskStore.getState().optimisticUpdate(data.task_id!, {
          ...(taskStatus ? { status: taskStatus } : {}),
          last_run_at: new Date().toISOString(),
          last_summary: data.summary || null,
          next_run_at: data.next_run_at || null,
        });
        // 重新拉取执行历史
        useScheduledTaskStore.getState().fetchRuns(data.task_id!);
      });
    },

  scheduled_task_failed: (_deps, msg) => {
      const data = (msg.data || msg.payload) as {
        task_id?: string;
        run_id?: string;
        task_status?: string;
        status?: string;
        reason?: string;
        consecutive_failures?: number;
        next_run_at?: string;
      };
      if (!data?.task_id) return;
      const validStatuses: TaskStatus[] = ['active', 'paused', 'error', 'running'];
      const taskStatus = data.task_status !== undefined
        ? (validStatuses.includes(data.task_status as TaskStatus)
          ? data.task_status as TaskStatus : undefined)
        : (validStatuses.includes(data.status as TaskStatus)
          ? data.status as TaskStatus : undefined);
      logger.warn('ws:scheduled-task', 'failed', data);
      import('../stores/useScheduledTaskStore').then(({ useScheduledTaskStore }) => {
        useScheduledTaskStore.getState().optimisticUpdate(data.task_id!, {
          ...(taskStatus ? { status: taskStatus } : {}),
          consecutive_failures: data.consecutive_failures || 0,
          next_run_at: data.next_run_at || null,
        });
        useScheduledTaskStore.getState().fetchRuns(data.task_id!);
      });
    },

  scheduled_task_notification: (_deps, msg) => {
      const data = (msg.data || msg.payload) as {
        task_id?: string;
        task_name?: string;
        level?: string;
        message?: string;
      };
      if (!data?.message) return;
      logger.warn('ws:scheduled-task', 'notification', data);
      // 这里只记日志，实际 UI 提示由 toast 组件处理（如果有）
    },
};

export function createWSMessageHandlers(deps: HandlerDeps): Record<string, (msg: WSMessage) => void> {
  const projection = createMessageStreamProjection(deps);
  return Object.fromEntries(
    Object.entries(handlerDefinitions).map(([type, handler]) => [
      type,
      (msg: WSMessage) => handler(deps, msg as WSIncomingMessage, projection),
    ]),
  );
}
