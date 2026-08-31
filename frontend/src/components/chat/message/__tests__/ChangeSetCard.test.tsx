import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { ChangeSet } from '../../../../types/changeset';
import ChangeSetCard from '../ChangeSetCard';
import { changeSetService } from '../../../../services/changeSet';

vi.mock('../../../../services/changeSet', () => ({
  changeSetService: {
    get: vi.fn(),
    timeline: vi.fn(),
    cancel: vi.fn(),
    confirm: vi.fn(),
    recover: vi.fn(),
  },
}));

function makeChangeSet(overrides: Partial<ChangeSet> = {}): ChangeSet {
  return {
    id: 'change-1', org_id: 'org-1', resource_type: 'scheduled_task', resource_id: 'task-1',
    operation: 'update', base_revision: 'r1', base_snapshot: { name: '旧任务', prompt: '旧内容' },
    proposed_snapshot: { name: '新任务', prompt: '一段很长的执行内容' },
    patch: [], diff: {}, risk_level: 'medium', policy_snapshot: { requires_approval: true },
    plan_snapshot: { steps: [{ intent: '先进行只读试跑' }, { intent: '等待确认后提交' }] },
    status: 'awaiting_approval', idempotency_key: 'key-1', expires_at: '2026-09-01T00:00:00Z',
    created_by: 'user-1', created_by_type: 'user', audit_subject: {}, revision: 2,
    created_at: '2026-08-30T10:00:00Z', updated_at: '2026-08-30T10:01:00Z',
    checks: [{ id: 'check-1', change_set_id: 'change-1', check_type: 'preflight', check_key: 'safe', input: {}, result: {}, status: 'passed', created_at: '2026-08-30T10:01:00Z' }],
    ...overrides,
  };
}

describe('ChangeSetCard', () => {
  beforeEach(() => {
    vi.mocked(changeSetService.get).mockResolvedValue(makeChangeSet());
    vi.mocked(changeSetService.timeline).mockResolvedValue({ change_set_id: 'change-1', events: [{
      id: 'event-1', change_set_id: 'change-1', sequence: 1, event_type: 'proposed',
      to_status: 'proposed', payload: {}, created_at: '2026-08-30T10:00:00Z',
    }] });
    vi.mocked(changeSetService.cancel).mockResolvedValue(makeChangeSet({ status: 'cancelled' }));
    vi.mocked(changeSetService.confirm).mockResolvedValue(makeChangeSet({ status: 'applied' }));
    vi.mocked(changeSetService.recover).mockResolvedValue(makeChangeSet({ id: 'change-2', status: 'draft' }));
  });

  afterEach(() => vi.clearAllMocks());

  it('reads the current ChangeSet and renders generic sections plus scheduled-task labels', async () => {
    render(<ChangeSetCard changeSetId="change-1" />);

    expect(await screen.findByRole('heading', { name: '编辑定时任务' })).toBeInTheDocument();
    expect(screen.getByText('将修改「新任务」的配置和执行路径')).toBeInTheDocument();
    expect(screen.getByText('待确认')).toBeInTheDocument();
    expect(screen.getAllByText('一段很长的执行内容').length).toBeGreaterThan(0);
    expect(screen.getByText('只读试跑')).toBeInTheDocument();
    expect(screen.getByText('确认提交')).toBeInTheDocument();
  });

  it('prevents duplicate cancellation while the first action is pending', async () => {
    let resolveCancel: ((value: ChangeSet) => void) | undefined;
    vi.mocked(changeSetService.cancel).mockImplementation(() => new Promise((resolve) => { resolveCancel = resolve; }));
    render(<ChangeSetCard changeSetId="change-1" />);
    const cancel = await screen.findByRole('button', { name: '取消变更' });

    fireEvent.click(cancel);
    fireEvent.click(cancel);
    expect(changeSetService.cancel).toHaveBeenCalledTimes(1);
    expect(cancel).toBeDisabled();

    resolveCancel?.(makeChangeSet({ status: 'cancelled' }));
    expect(await screen.findByText('这次变更已取消，未继续提交。')).toBeInTheDocument();
  });

  it('confirms through the ChangeSet API and renders the applied result', async () => {
    render(<ChangeSetCard changeSetId="change-1" />);
    fireEvent.click(await screen.findByRole('button', { name: '确认提交' }));

    await waitFor(() => expect(changeSetService.confirm).toHaveBeenCalledWith('change-1'));
    expect(await screen.findByText('变更已提交并记录完成结果。')).toBeInTheDocument();
  });

  it('recovers a failed ChangeSet with a new id and does not expose internal errors', async () => {
    vi.mocked(changeSetService.get).mockResolvedValue(makeChangeSet({ status: 'failed', error_message: 'DatabaseError: secret' }));
    render(<ChangeSetCard changeSetId="change-1" />);
    const retry = await screen.findByRole('button', { name: '重新尝试' });
    fireEvent.click(retry);

    await waitFor(() => expect(changeSetService.recover).toHaveBeenCalledWith('change-1'));
    expect(screen.queryByText(/DatabaseError|secret/)).not.toBeInTheDocument();
  });

  it('shows the conflict recovery guidance and emits replan without guessing a state', async () => {
    vi.mocked(changeSetService.get).mockResolvedValue(makeChangeSet({ status: 'conflicted' }));
    const listener = vi.fn();
    window.addEventListener('changeset:action', listener);
    render(<ChangeSetCard changeSetId="change-1" />);
    expect(await screen.findByText('任务已被更新，请基于最新版本重新规划。')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '重新规划' }));
    expect(listener).toHaveBeenCalledWith(expect.objectContaining({ detail: expect.objectContaining({ action: 'replan', changeSetId: 'change-1' }) }));
    window.removeEventListener('changeset:action', listener);
  });

  it('offers a new plan for a rejected preflight and delegates it to the host', async () => {
    vi.mocked(changeSetService.get).mockResolvedValue(makeChangeSet({ status: 'rejected' }));
    const replan = vi.fn().mockResolvedValue(makeChangeSet({ id: 'change-2', status: 'awaiting_approval' }));
    render(<ChangeSetCard changeSetId="change-1" actionHandlers={{ replan }} />);

    fireEvent.click(await screen.findByRole('button', { name: '重新规划' }));
    await waitFor(() => expect(replan).toHaveBeenCalledWith(expect.objectContaining({ id: 'change-1' })));
  });

  it('renders deletion as a destructive confirmation instead of an editing plan', async () => {
    vi.mocked(changeSetService.get).mockResolvedValue(makeChangeSet({
      operation: 'delete', risk_level: 'high',
      base_snapshot: { name: '日报任务', status: 'active', cron_expr: '0 9 * * *' },
      proposed_snapshot: { name: '日报任务', status: 'active', cron_expr: '0 9 * * *' },
    }));
    render(<ChangeSetCard changeSetId="change-1" />);

    expect(await screen.findByRole('heading', { name: '删除定时任务' })).toBeInTheDocument();
    expect(screen.getByRole('alert')).toHaveTextContent('删除后该任务将停止执行，且不能从此处恢复。');
    expect(screen.getByRole('button', { name: '确认删除' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '保留任务' })).toBeInTheDocument();
    expect(screen.queryByText('AI 规划的执行路径')).not.toBeInTheDocument();
    expect(screen.queryByText(/^Diff/)).not.toBeInTheDocument();
  });

  it('renders pause as a state confirmation without an AI plan or read-only trial claim', async () => {
    vi.mocked(changeSetService.get).mockResolvedValue(makeChangeSet({
      operation: 'pause',
      base_snapshot: { name: '日报任务', status: 'active', next_run_at: '2026-09-01T01:00:00Z' },
      proposed_snapshot: { name: '日报任务', status: 'paused', next_run_at: null },
    }));
    render(<ChangeSetCard changeSetId="change-1" />);

    expect(await screen.findByRole('heading', { name: '暂停定时任务' })).toBeInTheDocument();
    expect(screen.getByText('状态变化 · 2 项')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '确认暂停' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '保持启用' })).toBeInTheDocument();
    expect(screen.queryByText('AI 规划的执行路径')).not.toBeInTheDocument();
    expect(screen.queryByText('校验与只读试跑')).not.toBeInTheDocument();
  });

  it('re-reads after a ChangeSet update event, including after a conversation switch', async () => {
    const onChangeSetUpdated = vi.fn();
    render(<ChangeSetCard changeSetId="change-1" onChangeSetUpdated={onChangeSetUpdated} />);
    await screen.findByText('待确认');
    const callsBefore = vi.mocked(changeSetService.get).mock.calls.length;
    onChangeSetUpdated.mockClear();
    await act(async () => {
      window.dispatchEvent(new CustomEvent('changeset:updated', { detail: { changeSetId: 'change-1' } }));
    });
    await waitFor(() => expect(changeSetService.get.mock.calls.length).toBeGreaterThan(callsBefore));
    await waitFor(() => expect(onChangeSetUpdated).toHaveBeenCalledWith(expect.objectContaining({ id: 'change-1' })));
  });
});
