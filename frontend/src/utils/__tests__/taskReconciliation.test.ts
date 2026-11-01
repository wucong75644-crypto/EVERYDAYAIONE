import type { Message } from '../../stores/useMessageStore';
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { reconcileChatTaskStates, type PendingTask } from '../taskRestoration';

const mocks = vi.hoisted(() => ({
  getMessages: vi.fn(),
  fetchTasks: vi.fn(),
  store: {
    streamingMessages: new Map<string, string>(),
    messages: {} as Record<string, Message[]>,
    clearConversationStreaming: vi.fn(),
    markForceRefresh: vi.fn(),
    setMessagesForConversation: vi.fn(),
  },
}));

vi.mock('../../stores/useMessageStore', () => ({
  useMessageStore: { getState: () => mocks.store },
  normalizeMessage: (message: unknown) => message,
}));

vi.mock('../../services/message', () => ({
  getMessages: mocks.getMessages,
}));

vi.mock('../../services/api', () => ({
  default: { get: mocks.fetchTasks },
}));

vi.mock('react-hot-toast', () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

vi.mock('../logger', () => ({
  logger: {
    info: vi.fn(),
    debug: vi.fn(),
    warn: vi.fn(),
    error: vi.fn(),
  },
}));

function task(overrides: Partial<PendingTask>): PendingTask {
  return {
    id: 'task-1',
    external_task_id: 'external-1',
    conversation_id: 'conv-1',
    type: 'chat',
    status: 'completed',
    request_params: {},
    credits_locked: 0,
    placeholder_message_id: 'msg-1',
    placeholder_created_at: null,
    started_at: new Date().toISOString(),
    last_polled_at: null,
    ...overrides,
  };
}

describe('reconcileChatTaskStates', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.store.messages = {};
    mocks.store.streamingMessages.clear();
    mocks.store.clearConversationStreaming.mockImplementation((cid: string) => mocks.store.streamingMessages.delete(cid));
    mocks.store.setMessagesForConversation.mockImplementation((cid: string, messages: Message[]) => { mocks.store.messages[cid] = messages; });
    mocks.getMessages.mockResolvedValue({
      messages: [{ id: 'msg-1', role: 'assistant', status: 'completed', content: [] }],
    });
  });

  it('reloads messages and clears transient UI for completed tasks', async () => {
    await reconcileChatTaskStates([task({ status: 'completed' })]);

    expect(mocks.store.clearConversationStreaming).toHaveBeenCalledWith('conv-1');
    expect(mocks.store.markForceRefresh).toHaveBeenCalledWith('conv-1');
    expect(mocks.getMessages).toHaveBeenCalledWith('conv-1', 30, 0);
    expect(mocks.store.setMessagesForConversation).toHaveBeenCalledWith(
      'conv-1',
      expect.any(Array),
      false,
    );
  });

  it('reconciles paused tasks without restoring them as streaming', async () => {
    await reconcileChatTaskStates([task({ status: 'paused' })]);

    expect(mocks.store.clearConversationStreaming).toHaveBeenCalledWith('conv-1');
    expect(mocks.getMessages).toHaveBeenCalledWith('conv-1', 30, 0);
  });

  it('does not let an old terminal task clear a newer active task', async () => {
    await reconcileChatTaskStates([
      task({ id: 'old', status: 'completed' }),
      task({ id: 'new', status: 'running' }),
    ]);

    expect(mocks.store.clearConversationStreaming).not.toHaveBeenCalled();
    expect(mocks.getMessages).not.toHaveBeenCalled();
  });
  it('reads unchanged completed history once while another conversation streams', async () => {
    mocks.store.streamingMessages.set('other-conversation', 'live-reply');
    await reconcileChatTaskStates([task({})]);
    await reconcileChatTaskStates([task({})]);
    expect(mocks.getMessages).toHaveBeenCalledTimes(1);
    expect(mocks.store.streamingMessages.get('other-conversation')).toBe('live-reply');
  });

  it('queues concurrent snapshots and shares the successful history reconciliation', async () => {
    let resolve!: (value: { messages: unknown[] }) => void;
    mocks.getMessages.mockReturnValueOnce(new Promise(r => { resolve = r; }));
    const first = reconcileChatTaskStates([task({})]);
    const second = reconcileChatTaskStates([task({})]);
    await vi.waitFor(() => expect(mocks.getMessages).toHaveBeenCalledTimes(1));
    resolve({ messages: [] });
    await Promise.all([first, second]);
    expect(mocks.getMessages).toHaveBeenCalledTimes(1);
  });

  it('retries failed reads and invalidates receipts after cache removal', async () => {
    mocks.getMessages.mockRejectedValueOnce(new Error('offline'));
    await reconcileChatTaskStates([task({})]);
    await reconcileChatTaskStates([task({})]);
    delete mocks.store.messages['conv-1'];
    await reconcileChatTaskStates([task({})]);
    expect(mocks.getMessages).toHaveBeenCalledTimes(3);
  });

  it('a continued task and a new paused revision can reconcile again', async () => {
    await reconcileChatTaskStates([task({ status: 'paused', message_context_revision: 1 })]);
    await reconcileChatTaskStates([task({ status: 'running' })]);
    await reconcileChatTaskStates([task({ status: 'paused', message_context_revision: 2 })]);
    expect(mocks.getMessages).toHaveBeenCalledTimes(2);
  });

  it('shares timer and reconnect fetches that overlap', async () => {
    const token = vi.spyOn(Storage.prototype, 'getItem').mockReturnValue('local-test-token');
    let resolve!: (value: { data: { tasks: PendingTask[] } }) => void;
    mocks.fetchTasks.mockReturnValueOnce(new Promise(r => { resolve = r; }));
    try {
      const timer = reconcileChatTaskStates();
      const reconnect = reconcileChatTaskStates();
      expect(timer).toBe(reconnect);
      await vi.waitFor(() => expect(mocks.fetchTasks).toHaveBeenCalledTimes(1));
      resolve({ data: { tasks: [task({})] } });
      await Promise.all([timer, reconnect]);
      expect(mocks.getMessages).toHaveBeenCalledTimes(1);
    } finally { token.mockRestore(); }
  });

  it('does not overwrite a resumed stream that reuses the same message ID', async () => {
    mocks.store.streamingMessages.set('conv-1', 'msg-1');
    let resolve!: (value: { messages: unknown[] }) => void;
    mocks.getMessages.mockReturnValueOnce(new Promise(r => { resolve = r; }));
    const pending = reconcileChatTaskStates([task({ status: 'paused' })]);
    await vi.waitFor(() => expect(mocks.getMessages).toHaveBeenCalledTimes(1));
    mocks.store.streamingMessages = new Map([['conv-1', 'msg-1']]);
    resolve({ messages: [] });
    await pending;
    expect(mocks.store.clearConversationStreaming).not.toHaveBeenCalled();
    expect(mocks.store.setMessagesForConversation).not.toHaveBeenCalled();
  });

});
