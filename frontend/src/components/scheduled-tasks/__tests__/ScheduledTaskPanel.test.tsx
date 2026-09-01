import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { ReactNode } from 'react';
import type { ChangeSet } from '../../../types/changeset';
import ScheduledTaskPanel from '../ScheduledTaskPanel';
import { changeSetService } from '../../../services/changeSet';

const fetchTasks = vi.fn();

vi.mock('framer-motion', () => ({
  AnimatePresence: ({ children }: { children: ReactNode }) => children,
  m: { div: 'div', aside: 'aside', button: 'button' },
}));

vi.mock('../../../stores/useScheduledTaskStore', () => ({
  useScheduledTaskStore: (selector: (state: Record<string, unknown>) => unknown) => selector({
    tasks: [], loading: false, fetchTasks,
  }),
}));

vi.mock('../../../services/changeSet', () => ({
  changeSetService: { listActive: vi.fn() },
}));

vi.mock('../../../services/scheduledTask', () => ({
  scheduledTaskService: { proposeChange: vi.fn() },
}));

vi.mock('../TaskList', () => ({
  TaskList: () => <div data-testid="task-list">任务列表</div>,
}));

vi.mock('../ViewSwitcher', () => ({
  ViewSwitcher: () => null,
}));

vi.mock('../TaskForm', () => ({
  TaskForm: () => <div>任务表单</div>,
}));

vi.mock('../../chat/message/ChangeSetCard', () => ({
  default: ({ changeSetId }: { changeSetId: string }) => <div>变更卡片:{changeSetId}</div>,
}));

function makeChangeSet(overrides: Partial<ChangeSet> = {}): ChangeSet {
  return {
    id: 'change-1', org_id: 'org-1', resource_type: 'scheduled_task', resource_id: 'task-1',
    operation: 'update', base_revision: '1', base_snapshot: {}, proposed_snapshot: { name: '历史日报变更' },
    patch: [], diff: {}, risk_level: 'low', policy_snapshot: {}, status: 'awaiting_approval',
    idempotency_key: 'key-1', expires_at: '2030-01-01T00:00:00Z', created_by: 'user-1',
    created_by_type: 'user', audit_subject: {}, revision: 1,
    created_at: '2026-09-01T00:00:00Z', updated_at: '2026-09-01T00:00:00Z', checks: [],
    ...overrides,
  };
}

describe('ScheduledTaskPanel ChangeSet recovery', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(changeSetService.listActive).mockResolvedValue([makeChangeSet()]);
  });

  it('keeps the task list as the default entry even when historical changes are pending', async () => {
    render(<ScheduledTaskPanel isOpen onClose={vi.fn()} />);

    await waitFor(() => expect(changeSetService.listActive).toHaveBeenCalledWith('scheduled_task'));
    expect(screen.getByTestId('task-list')).toBeInTheDocument();
    expect(screen.queryByText('确认定时任务变更')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: '待处理变更 (1)' })).toBeInTheDocument();
  });

  it('opens a recovered ChangeSet only after the user selects it', async () => {
    render(<ScheduledTaskPanel isOpen onClose={vi.fn()} />);

    const pending = await screen.findByRole('button', { name: '待处理变更 (1)' });
    fireEvent.click(pending);
    fireEvent.click(screen.getByRole('button', { name: /历史日报变更.*待确认.*update/ }));

    expect(screen.getByText('变更卡片:change-1')).toBeInTheDocument();
  });

  it('returns to the task list when the panel is closed and opened again', async () => {
    const { rerender } = render(<ScheduledTaskPanel isOpen onClose={vi.fn()} />);
    fireEvent.click(await screen.findByRole('button', { name: '待处理变更 (1)' }));
    fireEvent.click(screen.getByRole('button', { name: /历史日报变更.*待确认.*update/ }));
    expect(screen.getByText('变更卡片:change-1')).toBeInTheDocument();

    rerender(<ScheduledTaskPanel isOpen={false} onClose={vi.fn()} />);
    rerender(<ScheduledTaskPanel isOpen onClose={vi.fn()} />);

    expect(screen.getByTestId('task-list')).toBeInTheDocument();
  });
});
