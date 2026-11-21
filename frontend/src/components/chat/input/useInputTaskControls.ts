import { useCallback, useEffect, useRef } from 'react';
import { pauseTaskByMessageId } from '../../../services/message';
import { useMessageStore } from '../../../stores/useMessageStore';
import { logger } from '../../../utils/logger';

export interface SteerAttachment {
  kind: 'image' | 'file';
  workspace_path: string;
}

interface UseInputTaskControlsOptions {
  conversationId: string | null;
  isStreaming: boolean;
  streamingMessageId: string | null;
}

export function useInputTaskControls({
  conversationId,
  isStreaming,
  streamingMessageId,
}: UseInputTaskControlsOptions) {
  const updateLocalInterruptedMessage = useCallback((reason: 'user_cancel' | 'user_pause') => {
    if (!streamingMessageId || !conversationId) return;

    const store = useMessageStore.getState();
    const thinkingText = store.streamingThinking.get(conversationId);
    if (thinkingText) {
      const message = store.getMessage(streamingMessageId);
      const committedLength = message?.content
        ?.filter(part => part.type === 'thinking')
        .reduce(
          (sum, part) => sum + (
            'text' in part && typeof part.text === 'string' ? part.text.length : 0
          ),
          0,
        ) ?? 0;
      const livePart = thinkingText.slice(committedLength);
      if (livePart.trim()) {
        store.appendContentBlock(conversationId, {
          type: 'thinking',
          text: livePart,
        });
      }
    }

    const cancelledAt = new Date().toISOString();
    const message = store.getMessage(streamingMessageId);
    if (message && Array.isArray(message.content)) {
      const content = message.content.map(part => {
        if (part.type === 'tool_step' && part.status === 'running') {
          return { ...part, status: 'cancelled' as const, cancelled_at: cancelledAt };
        }
        return part;
      });
      content.push({
        type: 'interrupt_marker',
        interrupted_at: cancelledAt,
        reason,
      });
      store.updateMessage(streamingMessageId, {
        status: 'interrupted',
        content,
      });
    } else {
      store.updateMessage(streamingMessageId, { status: 'interrupted' });
    }

    store.completeStreaming(conversationId);
  }, [streamingMessageId, conversationId]);

  const handleStop = useCallback(() => {
    if (!streamingMessageId || !conversationId) return;
    // 生成中的显式停止是“暂停并保存进度”，让用户可以通过“继续”恢复。
    // 最终取消只允许通过语义指令“取消/放弃”进入 cancel RPC，避免
    // 旧的按钮入口把可恢复任务提前变成不可恢复的 cancelled。
    updateLocalInterruptedMessage('user_pause');
    pauseTaskByMessageId(streamingMessageId).catch(error => {
      logger.error('inputArea', '暂停任务失败', error);
    });
  }, [streamingMessageId, conversationId, updateLocalInterruptedMessage]);

  const handlePause = useCallback(() => {
    if (!streamingMessageId || !conversationId) return;
    updateLocalInterruptedMessage('user_pause');
    pauseTaskByMessageId(streamingMessageId).catch(error => {
      logger.error('inputArea', '暂停任务失败', error);
    });
  }, [streamingMessageId, conversationId, updateLocalInterruptedMessage]);

  useEffect(() => {
    if (!isStreaming) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== 'Escape' || document.querySelector('[role="dialog"]')) return;
      event.preventDefault();
      handleStop();
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [isStreaming, handleStop]);

  const pendingSteer = useRef<{ taskId: string; conversationId: string; message: string; attachments?: SteerAttachment[]; requestId: string } | null>(null);
  const hasPendingSteer = useCallback((message: string, attachments?: SteerAttachment[]) =>
    pendingSteer.current?.conversationId === conversationId && pendingSteer.current?.message === message
    && JSON.stringify(pendingSteer.current?.attachments) === JSON.stringify(attachments), [conversationId]);

  const sendSteer = useCallback((message: string, attachments?: SteerAttachment[]): Promise<boolean> | null => {
    if (!conversationId) return null;
    const previous = hasPendingSteer(message, attachments) ? pendingSteer.current : null;
    const streamingMessage = streamingMessageId
      ? useMessageStore.getState().getMessage(streamingMessageId)
      : undefined;
    const taskId = previous?.taskId || streamingMessage?.task_id;
    if (!taskId) return null;
    const command = previous ?? { taskId, conversationId, message, attachments, requestId: crypto.randomUUID() };
    pendingSteer.current = command;

    return new Promise(resolve => {
      const finish = (accepted: boolean) => {
        window.clearTimeout(timer);
        window.removeEventListener('chat:steer-result', receive);
        if (accepted && pendingSteer.current === command) pendingSteer.current = null;
        resolve(accepted);
      };
      const receive = (event: Event) => {
        const result = (event as CustomEvent).detail;
        if (result.task_id !== taskId || result.conversation_id !== conversationId
            || result.payload?.request_id !== command.requestId) return;
        finish(result.payload.outcome === 'enqueued' || result.payload.outcome === 'control_applied');
      };
      const timer = window.setTimeout(() => finish(false), 8_000);
      window.addEventListener('chat:steer-result', receive);
      window.dispatchEvent(new CustomEvent('chat:user-steer', {
        detail: { ...command, retry: previous !== null, onUnsent: () => finish(false) },
      }));
    });
  }, [conversationId, hasPendingSteer, streamingMessageId]);

  return { handleStop, handlePause, sendSteer, hasPendingSteer };
}
