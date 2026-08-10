import { beforeEach, describe, expect, it, vi } from 'vitest';

import { createWSMessageHandlers, type HandlerDeps } from '../wsMessageHandlers';


const mockScheduledTaskState = {
  optimisticUpdate: vi.fn(),
  fetchRuns: vi.fn(),
};

vi.mock('../../stores/useScheduledTaskStore', () => ({
  useScheduledTaskStore: {
    getState: () => mockScheduledTaskState,
  },
}));

vi.mock('../../utils/logger', () => ({
  logger: {
    info: vi.fn(), debug: vi.fn(), warn: vi.fn(), error: vi.fn(),
  },
}));


describe('scheduled task projection wakeups', () => {
  const handlers = createWSMessageHandlers({} as HandlerDeps);

  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('uses the durable completed task_status instead of forcing active', async () => {
    handlers.scheduled_task_completed({
      data: {
        task_id: 'task_once', run_id: 'run_1', task_status: 'paused',
        status: 'success', summary: 'safe summary', next_run_at: null,
      },
    });

    await vi.waitFor(() => expect(mockScheduledTaskState.optimisticUpdate)
      .toHaveBeenCalledWith('task_once', expect.objectContaining({
        status: 'paused', last_summary: 'safe summary', next_run_at: null,
      })));
    expect(mockScheduledTaskState.fetchRuns).toHaveBeenCalledWith('task_once');
  });

  it('uses only allowlisted failed task_status and still refreshes runs', async () => {
    handlers.scheduled_task_failed({
      data: {
        task_id: 'task_failed', run_id: 'run_2', task_status: 'error',
        status: 'failed', reason: 'redacted_terminal_reason',
        consecutive_failures: 3,
      },
    });

    await vi.waitFor(() => expect(mockScheduledTaskState.optimisticUpdate)
      .toHaveBeenCalledWith('task_failed', expect.objectContaining({
        status: 'error', consecutive_failures: 3,
      })));
    expect(mockScheduledTaskState.fetchRuns).toHaveBeenCalledWith('task_failed');
  });

  it('does not trust an unknown task_status', async () => {
    handlers.scheduled_task_completed({
      data: { task_id: 'task_unknown', task_status: 'compromised' },
    });

    await vi.waitFor(() => expect(mockScheduledTaskState.optimisticUpdate)
      .toHaveBeenCalled());
    const update = mockScheduledTaskState.optimisticUpdate.mock.calls[0][1];
    expect(update).not.toHaveProperty('status');
    expect(mockScheduledTaskState.fetchRuns).toHaveBeenCalledWith('task_unknown');
  });
});
