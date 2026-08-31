import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { ScheduledTask } from '../../../types/scheduledTask';
import { TaskCard } from '../TaskCard';

const runTaskNow = vi.fn();
const setExpandedTaskId = vi.fn();

vi.mock('../../../stores/useScheduledTaskStore', () => ({
  useScheduledTaskStore: (selector: (state: Record<string, unknown>) => unknown) => selector({
    runTaskNow,
    expandedTaskId: null,
    setExpandedTaskId,
    runs: {},
    fetchRuns: vi.fn(),
  }),
}));

vi.mock('../../../stores/useAuthStore', () => ({
  useAuthStore: (selector: (state: Record<string, unknown>) => unknown) => selector({ user: { id: 'user-1' } }),
}));

vi.mock('../../../hooks/usePermission', () => ({
  usePermission: () => true,
  useCanExecuteTask: () => false,
}));

function makeTask(overrides: Partial<ScheduledTask> = {}): ScheduledTask {
  return {
    id: 'task-1', org_id: 'org-1', user_id: 'user-1', name: '销售日报', prompt: '汇总销售数据',
    schedule_type: 'daily', cron_expr: '0 9 * * *', timezone: 'Asia/Shanghai',
    push_target: { type: 'web', user_id: 'user-1' }, status: 'active', max_credits: 10,
    retry_count: 1, timeout_sec: 180, run_count: 0, consecutive_failures: 0,
    created_at: '2026-08-30T00:00:00Z', updated_at: '2026-08-30T00:00:00Z', revision: '1',
    ...overrides,
  };
}

describe('TaskCard ChangeSet actions', () => {
  beforeEach(() => vi.clearAllMocks());

  it('routes pause and delete through a proposal instead of mutating the task optimistically', async () => {
    const onChangeRequested = vi.fn().mockResolvedValue(undefined);
    const task = makeTask();
    render(<TaskCard task={task} onChangeRequested={onChangeRequested} />);

    fireEvent.click(screen.getByRole('button', { name: '暂停' }));
    await waitFor(() => expect(onChangeRequested).toHaveBeenCalledWith('pause', task));

    fireEvent.click(screen.getByRole('button', { name: '删除' }));
    await waitFor(() => expect(onChangeRequested).toHaveBeenCalledWith('delete', task));
    expect(onChangeRequested).toHaveBeenCalledTimes(2);
  });

  it('prevents duplicate proposals while a request is pending and renders a safe error on failure', async () => {
    let rejectProposal: ((reason?: unknown) => void) | undefined;
    const onChangeRequested = vi.fn().mockImplementation(() => new Promise<void>((_resolve, reject) => {
      rejectProposal = reject;
    }));
    render(<TaskCard task={makeTask()} onChangeRequested={onChangeRequested} />);

    const pause = screen.getByRole('button', { name: '暂停' });
    fireEvent.click(pause);
    fireEvent.click(pause);
    expect(onChangeRequested).toHaveBeenCalledTimes(1);
    expect(pause).toBeDisabled();

    rejectProposal?.(new Error('internal details must not render'));
    expect(await screen.findByRole('alert')).toHaveTextContent('暂时无法生成变更方案，请稍后重试。');
    expect(screen.queryByText(/internal details/)).not.toBeInTheDocument();
  });
});
