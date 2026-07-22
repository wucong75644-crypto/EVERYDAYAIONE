import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const store = {
  addMessage: vi.fn(),
  updateMessage: vi.fn(),
  removeMessage: vi.fn(),
  startStreaming: vi.fn(),
  completeStreaming: vi.fn(),
  setIsSending: vi.fn(),
  registerStreamingId: vi.fn(),
  getMessage: vi.fn(),
  createTask: vi.fn(),
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

const identifiers = {
  clientRequestId: 'request-1',
  userMessageId: 'user-1',
  assistantMessageId: 'assistant-1',
  clientTaskId: 'task-1',
};

const response = {
  task_id: 'task-1',
  user_message: null,
  assistant_message: {
    id: 'assistant-1', conversation_id: 'conv-1', role: 'assistant',
    content: [], status: 'pending', created_at: '2026-07-16T00:00:00.000Z',
  },
  operation: 'send',
  generation_type: 'chat',
};

describe('sendMessage idempotent retry', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('retries a timeout with the same IDs and one optimistic update', async () => {
    requestMock
      .mockRejectedValueOnce(new ApiRequestError(
        'REQUEST_TIMEOUT', '请求超时', undefined, undefined, 'timeout',
      ))
      .mockResolvedValueOnce(response);
    const subscribeTask = vi.fn();

    const pending = sendMessage({
      conversationId: 'conv-1', content: [{ type: 'text', text: 'hello' }],
      identifiers, subscribeTask,
    });
    await vi.runAllTimersAsync();
    await expect(pending).resolves.toBe('task-1');

    expect(requestMock).toHaveBeenCalledTimes(2);
    expect(requestMock.mock.calls[0][0]).toEqual(requestMock.mock.calls[1][0]);
    expect(requestMock.mock.calls[0][0]).toMatchObject({
      headers: { 'Idempotency-Key': 'request-1' },
      data: {
        client_request_id: 'request-1', client_task_id: 'task-1',
        assistant_message_id: 'assistant-1',
      },
    });
    expect(store.addMessage).toHaveBeenCalledTimes(1);
    expect(store.startStreaming).toHaveBeenCalledTimes(1);
    expect(subscribeTask).toHaveBeenCalledTimes(1);
  });

  it('keeps optimistic state when network outcome remains uncertain', async () => {
    requestMock.mockRejectedValue(new ApiRequestError(
      'NETWORK_ERROR', 'Network Error', undefined, undefined, 'network',
    ));
    const unsubscribeTask = vi.fn();

    const pending = sendMessage({
      conversationId: 'conv-1', content: [{ type: 'text', text: 'hello' }],
      identifiers, unsubscribeTask,
    });
    const rejection = expect(pending).rejects.toMatchObject({ sendDisposition: 'uncertain' });
    await vi.runAllTimersAsync();
    await rejection;

    expect(requestMock).toHaveBeenCalledTimes(3);
    expect(store.completeStreaming).not.toHaveBeenCalled();
    expect(store.removeMessage).not.toHaveBeenCalled();
    expect(unsubscribeTask).not.toHaveBeenCalled();
  });

  it('rolls back streaming state when backend returns an explicit 500', async () => {
    requestMock.mockRejectedValue(new ApiRequestError(
      'INTERNAL_ERROR', '服务器内部错误', 500, undefined, 'http',
    ));
    const unsubscribeTask = vi.fn();

    await expect(sendMessage({
      conversationId: 'conv-1', content: [{ type: 'text', text: 'hello' }],
      identifiers, unsubscribeTask,
    })).rejects.toMatchObject({ sendDisposition: 'rejected' });

    expect(store.completeStreaming).toHaveBeenCalledWith('conv-1');
    expect(store.setIsSending).toHaveBeenCalledWith(false);
    expect(unsubscribeTask).toHaveBeenCalledWith('task-1');
  });

  it('retries an in-progress idempotency claim after the backend delay', async () => {
    requestMock
      .mockRejectedValueOnce(new ApiRequestError(
        'IDEMPOTENCY_REQUEST_IN_PROGRESS', '请求处理中', 409, { retry_after: 1 },
      ))
      .mockResolvedValueOnce(response);

    const pending = sendMessage({
      conversationId: 'conv-1', content: [{ type: 'text', text: 'hello' }], identifiers,
    });
    await vi.advanceTimersByTimeAsync(999);
    expect(requestMock).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(1);
    await expect(pending).resolves.toBe('task-1');

    expect(requestMock).toHaveBeenCalledTimes(2);
    expect(requestMock.mock.calls[0][0]).toEqual(requestMock.mock.calls[1][0]);
  });

  it('retries an unstructured 503 with the same request', async () => {
    requestMock
      .mockRejectedValueOnce(new ApiRequestError('API_ERROR', 'Service Unavailable', 503))
      .mockResolvedValueOnce(response);

    const pending = sendMessage({
      conversationId: 'conv-1', content: [{ type: 'text', text: 'hello' }], identifiers,
    });
    await vi.runAllTimersAsync();
    await expect(pending).resolves.toBe('task-1');

    expect(requestMock).toHaveBeenCalledTimes(2);
    expect(requestMock.mock.calls[0][0]).toEqual(requestMock.mock.calls[1][0]);
  });

  it('does not retry an explicit business rejection', async () => {
    requestMock.mockRejectedValue(new ApiRequestError(
      'INSUFFICIENT_CREDITS', '积分不足', 402,
    ));

    await expect(sendMessage({
      conversationId: 'conv-1', content: [{ type: 'text', text: 'hello' }], identifiers,
    })).rejects.toMatchObject({ sendDisposition: 'rejected' });

    expect(requestMock).toHaveBeenCalledTimes(1);
    expect(store.removeMessage).toHaveBeenCalledWith('user-1');
  });
});
