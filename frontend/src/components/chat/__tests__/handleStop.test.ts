/**
 * handleStop 停止生成逻辑单元测试
 *
 * 覆盖：消息状态更新、流式状态清理、后端取消调用、空值守卫
 */

import { describe, it, expect, beforeEach, vi } from 'vitest';
import { create } from 'zustand';
import { createStreamingSlice, type StreamingSlice, type StreamingSliceDeps } from '../../../stores/slices/streamingSlice';

// Mock cancelTaskByMessageId
vi.mock('../../../services/message', () => ({
  cancelTaskByMessageId: vi.fn(() => Promise.resolve()),
}));

import { cancelTaskByMessageId } from '../../../services/message';

type TestStore = StreamingSlice & StreamingSliceDeps & {
  updateMessage: (messageId: string, data: Record<string, unknown>) => void;
};

function createTestStore() {
  return create<TestStore>()((set, get, api) => ({
    messages: {},
    updateMessage: vi.fn(),
    ...createStreamingSlice(set, get, api),
  }));
}

/**
 * 模拟 handleStop 逻辑（与 InputArea.tsx 中实现一致）
 */
function executeHandleStop(
  store: ReturnType<typeof createTestStore>,
  streamingMessageId: string | null,
  conversationId: string | null,
) {
  if (!streamingMessageId || !conversationId) return;

  store.getState().updateMessage(streamingMessageId, { status: 'completed' });
  store.getState().completeStreaming(conversationId);
  cancelTaskByMessageId(streamingMessageId).catch(() => {});
}

describe('handleStop - 停止生成逻辑', () => {
  let store: ReturnType<typeof createTestStore>;

  beforeEach(() => {
    store = createTestStore();
    vi.clearAllMocks();
  });

  it('should update message status to completed', () => {
    store.getState().startStreaming('conv_1', 'msg_1');

    executeHandleStop(store, 'msg_1', 'conv_1');

    expect(store.getState().updateMessage).toHaveBeenCalledWith('msg_1', { status: 'completed' });
  });

  it('should clear streaming state', () => {
    store.getState().startStreaming('conv_1', 'msg_1');
    expect(store.getState().streamingMessages.has('conv_1')).toBe(true);
    expect(store.getState().isSending).toBe(true);

    executeHandleStop(store, 'msg_1', 'conv_1');

    expect(store.getState().streamingMessages.has('conv_1')).toBe(false);
    expect(store.getState().isSending).toBe(false);
  });

  it('should call cancelTaskByMessageId with message id', () => {
    store.getState().startStreaming('conv_1', 'msg_1');

    executeHandleStop(store, 'msg_1', 'conv_1');

    expect(cancelTaskByMessageId).toHaveBeenCalledWith('msg_1');
  });

  it('should do nothing when streamingMessageId is null', () => {
    executeHandleStop(store, null, 'conv_1');

    expect(store.getState().updateMessage).not.toHaveBeenCalled();
    expect(cancelTaskByMessageId).not.toHaveBeenCalled();
  });

  it('should do nothing when conversationId is null', () => {
    executeHandleStop(store, 'msg_1', null);

    expect(store.getState().updateMessage).not.toHaveBeenCalled();
    expect(cancelTaskByMessageId).not.toHaveBeenCalled();
  });

  it('should also clear thinking and agentStepHint', () => {
    store.getState().startStreaming('conv_1', 'msg_1');
    store.getState().appendStreamingThinking('conv_1', '思考中...');
    store.getState().setAgentStepHint('conv_1', '搜索中...');

    executeHandleStop(store, 'msg_1', 'conv_1');

    expect(store.getState().streamingThinking.get('conv_1')).toBeUndefined();
    expect(store.getState().agentStepHint.get('conv_1')).toBeUndefined();
  });

  it('should preserve accumulated content in optimistic messages', () => {
    store.getState().startStreaming('conv_1', 'msg_1');
    store.getState().appendStreamingContent('conv_1', '已生成的内容');

    executeHandleStop(store, 'msg_1', 'conv_1');

    // optimistic message should still exist with content
    const messages = store.getState().optimisticMessages.get('conv_1');
    expect(messages).toBeDefined();
    expect(messages!.length).toBe(1);
    expect(messages![0].content[0]).toEqual({ type: 'text', text: '已生成的内容' });
  });
});
