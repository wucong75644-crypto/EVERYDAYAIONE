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
import {
  handleImagePartialUpdate,
  handleMessageDone,
  handleMessageError,
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

type HandlerDefinition = (deps: HandlerDeps, msg: WSIncomingMessage) => void;

const handlerDefinitions: Record<string, HandlerDefinition> = {
  message_start: (deps, msg) => {
      const { message_id } = msg;
      if (!message_id) return;

      logger.info('ws:message', 'start received', { messageId: message_id });
      deps.getStore().setStatus(message_id, 'streaming');
    },

  message_chunk: (deps, msg) => {
      const { message_id, task_id, conversation_id } = msg;
      const chunk = parseProtocolString(msg.chunk ?? msg.payload?.chunk, 'chunk', {
        messageId: message_id,
        conversationId: conversation_id,
        source: 'ws:message_chunk',
      });
      if (!message_id || !chunk || !conversation_id) return;

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

    // stream_end：LLM 流结束信号（对标 Anthropic message_stop）
    // 在 DB 持久化之前发送，前端立即退出 streaming 状态
  stream_end: (deps, msg) => {
      const { message_id, conversation_id } = msg;
      logger.info('ws:message', 'stream_end received', { messageId: message_id, conversationId: conversation_id });

      // flush 残留 chunk
      if (deps.chunkBufferRef.current.size > 0) {
        if (deps.flushTimerRef.current) {
          clearTimeout(deps.flushTimerRef.current);
          deps.flushTimerRef.current = null;
        }
        flushChunkBuffer(deps);
      }

      const store = deps.getStore();
      if (message_id) {
        store.setStatus(message_id, 'completed');
      }
      if (conversation_id) {
        store.completeStreaming(conversation_id);
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

  subscribed: (deps, msg) => {
      const payload = msg.payload || {};
      const task_id = typeof payload.task_id === 'string' ? payload.task_id : undefined;
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
        const conversationId = deps.taskConversationMapRef.current.get(task_id);
        if (conversationId) {
          if (accumulatedBlocks.length > 0) {
            const remaining = calcRemainingText(accumulatedBlocks, accumulated);
            deps.getStore().restoreStreamingBlocks(conversationId, accumulatedBlocks, remaining);
          } else if (accumulated && accumulated.length > 0) {
            // 向后兼容：无 blocks 时仅恢复纯文字
            deps.getStore().setStreamingContent(conversationId, accumulated);
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

  thinking_chunk: (deps, msg) => {
      const { conversation_id } = msg;
      const chunk = parseProtocolString(msg.chunk ?? msg.payload?.chunk, 'chunk', {
        conversationId: conversation_id,
        source: 'ws:thinking_chunk',
      });
      if (!conversation_id || !chunk) return;

      deps.getStore().appendStreamingThinking(conversation_id, chunk);
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

  content_block_add: (deps, msg) => {
      const { conversation_id } = msg;
      const block = parseContentPart(msg.payload?.block, {
        messageId: msg.message_id,
        conversationId: conversation_id,
        source: 'ws:content_block_add',
      });
      if (!conversation_id || !block) return;

      const store = deps.getStore();

      // tool_step 状态更新：非 running 的 tool_step 是对已有 block 的更新
      if (block.type === 'tool_step' && block.tool_call_id && block.status !== 'running') {
        store.updateContentBlock(conversation_id, block.tool_call_id, block);
        logger.info('ws:content', 'tool_step_update', {
          conversationId: conversation_id,
          toolCallId: block.tool_call_id,
          status: block.status,
        });
        return;
      }

      // text block 去重：message_chunk 已逐字累积出相同的 text block，
      // content_block_add 的 text 是完整版——替换最后一个 text block 而非追加
      if (block.type === 'text') {
        store.replaceLastTextBlock(conversation_id, block);
        logger.info('ws:content', 'text_block_replace', { conversationId: conversation_id });
        return;
      }

      // 其他 block（新增的 running tool_step、image、file 等）：直接追加
      store.appendContentBlock(conversation_id, block);
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
      if (!conversation_id || !toolCallId || !toolName) return;

      // 显示步骤提示
      deps.getStore().setAgentStepHint(conversation_id, `⚠ ${description || toolName} — 等待确认`);

      // 触发确认弹窗
      deps.getStore().setToolConfirmRequest({
        toolCallId,
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
        task_name?: string;
        next_run_at?: string;
        summary?: string;
        push_status?: string;
      };
      if (!data?.task_id) return;
      logger.info('ws:scheduled-task', 'completed', data);
      import('../stores/useScheduledTaskStore').then(({ useScheduledTaskStore }) => {
        useScheduledTaskStore.getState().optimisticUpdate(data.task_id!, {
          status: 'active',
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
        task_name?: string;
        status?: string;
        error?: string;
        consecutive_failures?: number;
        will_retry?: boolean;
      };
      if (!data?.task_id) return;
      const validStatuses: TaskStatus[] = ['active', 'paused', 'error', 'running'];
      const status = validStatuses.includes(data.status as TaskStatus)
        ? data.status as TaskStatus
        : 'error';
      logger.warn('ws:scheduled-task', 'failed', data);
      import('../stores/useScheduledTaskStore').then(({ useScheduledTaskStore }) => {
        useScheduledTaskStore.getState().optimisticUpdate(data.task_id!, {
          status,
          consecutive_failures: data.consecutive_failures || 0,
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
  return Object.fromEntries(
    Object.entries(handlerDefinitions).map(([type, handler]) => [
      type,
      (msg: WSMessage) => handler(deps, msg as WSIncomingMessage),
    ]),
  );
}
