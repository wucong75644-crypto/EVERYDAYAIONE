import { useCallback, useEffect } from 'react';
import { cancelTaskByMessageId } from '../../../services/message';
import { useMessageStore } from '../../../stores/useMessageStore';
import { logger } from '../../../utils/logger';

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
  const handleStop = useCallback(() => {
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
        reason: 'user_cancel',
      });
      store.updateMessage(streamingMessageId, {
        status: 'interrupted',
        content,
      });
    } else {
      store.updateMessage(streamingMessageId, { status: 'interrupted' });
    }

    store.completeStreaming(conversationId);
    cancelTaskByMessageId(streamingMessageId).catch(error => {
      logger.error('inputArea', '取消任务失败', error);
    });
  }, [streamingMessageId, conversationId]);

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

  const sendSteer = useCallback((message: string) => {
    const streamingMessage = streamingMessageId
      ? useMessageStore.getState().getMessage(streamingMessageId)
      : undefined;
    const taskId = streamingMessage?.task_id;
    if (!taskId || !conversationId) return;

    window.dispatchEvent(new CustomEvent('chat:user-steer', {
      detail: { taskId, conversationId, message },
    }));
    logger.info('inputArea', '发送打断信号', { taskId, msgLen: message.length });
  }, [streamingMessageId, conversationId]);

  return { handleStop, sendSteer };
}
