import { act, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import type { TaskRun } from '../../../types/scheduledTask';
import { TaskRunHistory } from '../TaskRunHistory';

const state = vi.hoisted(() => ({
  tasks: [{ id: 't1', status: 'running' }],
  runs: {} as Record<string, TaskRun[]>,
  fetchRuns: vi.fn().mockResolvedValue(undefined),
  fetchTasks: vi.fn().mockResolvedValue(undefined),
}));
vi.mock('../../../stores/useScheduledTaskStore', () => ({
  useScheduledTaskStore: (select: (s: typeof state) => unknown) => select(state),
}));
vi.mock('../../chat/message/ChartBlock', () => ({ default: ({ title }: { title: string }) => <div>图表:{title}</div> }));
vi.mock('../../chat/media/FileCard', () => ({ default: ({ files }: { files: { url: string; name: string }[] }) => <a href={files[0].url}>{files[0].name}</a> }));

beforeEach(() => {
  vi.clearAllMocks();
  state.tasks = [{ id: 't1', status: 'running' }];
  state.runs = {};
});
afterEach(() => vi.useRealTimers());

it('shows stored summary, table, chart and download without treating all payloads as files', () => {
  state.tasks[0].status = 'paused';
  state.runs.t1 = [{ id: 'r1', task_id: 't1', org_id: 'o1', status: 'success', started_at: new Date().toISOString(), credits_used: 1, tokens_used: 10,
    result_summary: '按平台汇总完成', content_blocks: [
      { type: 'table', title: '平台订单', columns: ['平台', '数量'], rows: [{ 平台: '测试商城', 数量: 12 }] },
      { type: 'chart', title: '平台分布', option: {} },
      { type: 'file', url: '/report.csv', name: 'report.csv', mime_type: 'text/csv' },
    ],
  }];
  render(<TaskRunHistory taskId="t1" />);
  expect(screen.getByText('按平台汇总完成')).toBeVisible();
  expect(screen.getByRole('cell', { name: '测试商城' })).toBeVisible();
  expect(screen.getByText('图表:平台分布')).toBeVisible();
  expect(screen.getByRole('link', { name: 'report.csv' })).toHaveAttribute('href', '/report.csv');
  expect(screen.queryByText(/已启用/)).not.toBeInTheDocument();
});

it('recovers a missed completion event by polling while running and stops after completion/unmount', async () => {
  vi.useFakeTimers();
  const { rerender, unmount } = render(<TaskRunHistory taskId="t1" />);
  expect(state.fetchRuns).toHaveBeenCalledWith('t1');
  await act(() => vi.advanceTimersByTimeAsync(5000));
  expect(state.fetchRuns).toHaveBeenCalledTimes(2);
  expect(state.fetchTasks).toHaveBeenCalledWith({ quiet: true });
  state.tasks[0].status = 'paused';
  state.runs.t1 = [];
  rerender(<TaskRunHistory taskId="t1" />);
  await act(() => vi.advanceTimersByTimeAsync(15000));
  expect(state.fetchRuns).toHaveBeenCalledTimes(2);
  state.tasks[0].status = 'running';
  rerender(<TaskRunHistory taskId="t1" />);
  unmount();
  await act(() => vi.advanceTimersByTimeAsync(15000));
  expect(state.fetchRuns).toHaveBeenCalledTimes(2);
});

it('keeps legacy file links readable', () => {
  state.tasks[0].status = 'paused';
  state.runs.t1 = [{ id: 'r1', task_id: 't1', org_id: 'o1', status: 'success', started_at: new Date().toISOString(), credits_used: 0, tokens_used: 0,
    result_files: [{ url: '/old.csv', name: 'old.csv' }],
  }];
  render(<TaskRunHistory taskId="t1" />);
  expect(screen.getByRole('link', { name: 'old.csv' })).toHaveAttribute('href', '/old.csv');
});
