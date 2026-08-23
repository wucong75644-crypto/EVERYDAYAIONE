import { describe, it, expect, beforeEach, vi } from 'vitest';
import { reconcileChatTaskStates, type PendingTask } from '../taskRestoration';

const mocks = vi.hoisted(() => ({
  getMessages: vi.fn(),
  store: {
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
  default: { get: vi.fn() },
}));

vi.mock('react-hot-toast', () => ({
  default: { success: vi.fn(), error: vi.fn() },
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
});
