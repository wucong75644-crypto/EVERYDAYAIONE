import { useCallback, useEffect } from 'react';
import { pauseTaskByMessageId } from '../../../services/message';
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

  const sendSteer = useCallback((message: string): boolean => {
    const streamingMessage = streamingMessageId
      ? useMessageStore.getState().getMessage(streamingMessageId)
      : undefined;
    const taskId = streamingMessage?.task_id;
    if (!taskId || !conversationId) return false;

    window.dispatchEvent(new CustomEvent('chat:user-steer', {
      detail: { taskId, conversationId, message },
    }));
    logger.info('inputArea', '发送打断信号', { taskId, msgLen: message.length });
    return true;
  }, [streamingMessageId, conversationId]);

  return { handleStop, handlePause, sendSteer };
}
