import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { Message } from '../../stores/useMessageStore';

const store = {
  addMessage: vi.fn(),
  updateMessage: vi.fn(),
  removeMessage: vi.fn(),
  startStreaming: vi.fn(),
  completeStreaming: vi.fn(),
  setIsSending: vi.fn(),
  registerStreamingId: vi.fn(),
  getMessage: vi.fn(),
};
const requestMock = vi.fn();

vi.mock('../../stores/useMessageStore', () => ({
  useMessageStore: { getState: () => store },
}));

vi.mock('../api', async () => {
  const actual = await vi.importActual<typeof import('../api')>('../api');
  return { ...actual, request: (...args: unknown[]) => requestMock(...args) };
});

import { ApiRequestError } from '../api';
import { sendMessage } from '../messageSender';


describe('sendMessage error rollback', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.spyOn(crypto, 'randomUUID')
      .mockReturnValueOnce('client-request-id')
      .mockReturnValueOnce('user-message-id')
      .mockReturnValueOnce('assistant-message-id')
      .mockReturnValueOnce('client-task-id');
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('restores failed image when retry is rejected for insufficient credits', async () => {
    const original: Message = {
      id: 'failed-image',
      conversation_id: 'conv-1',
      role: 'assistant',
      status: 'failed',
      is_error: false,
      created_at: new Date().toISOString(),
      generation_params: { type: 'image', num_images: 1 },
      content: [{ type: 'image', url: null, failed: true }],
    };
    store.getMessage.mockReturnValue(original);
    requestMock.mockRejectedValue(new ApiRequestError(
      'INSUFFICIENT_CREDITS', '积分不足，需要 10 积分，当前余额 2 积分', 402,
    ));

    await expect(sendMessage({
      conversationId: 'conv-1', content: [], generationType: 'image',
      operation: 'retry', originalMessageId: 'failed-image',
    })).rejects.toThrow('积分不足');

    expect(store.updateMessage).toHaveBeenLastCalledWith('failed-image', original);
    expect(store.removeMessage).not.toHaveBeenCalled();
  });

  it('keeps image submission failures as failed image content', async () => {
    store.getMessage.mockReturnValue(undefined);
    requestMock.mockRejectedValue(new ApiRequestError(
      'IMAGE_GENERATION_FAILED', '图片生成服务暂时不可用，请稍后重试', 502,
    ));

    await expect(sendMessage({
      conversationId: 'conv-1',
      content: [{ type: 'text', text: '画一只猫' }],
      generationType: 'image',
      params: { num_images: 2 },
    })).rejects.toThrow('图片生成服务暂时不可用');

    expect(store.updateMessage).toHaveBeenLastCalledWith(
      'assistant-message-id',
      expect.objectContaining({
        status: 'failed',
        is_error: false,
        content: [
          expect.objectContaining({ type: 'image', failed: true }),
          expect.objectContaining({ type: 'image', failed: true }),
        ],
      }),
    );
  });
});
