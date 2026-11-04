import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import TaskProgressCard from '../TaskProgressCard';
import { pauseTaskByMessageId, cancelTaskByMessageId } from '../../../../services/message';
vi.mock('../../../../services/message', () => ({ pauseTaskByMessageId: vi.fn(), cancelTaskByMessageId: vi.fn() }));
import { getTaskProgress, type TaskProgress } from '../../../../services/taskExecution';

vi.mock('../../../../services/taskExecution', () => ({ getTaskProgress: vi.fn() }));
const visibility = vi.hoisted(() => ({ inView: true }));
vi.mock('react-intersection-observer', () => ({ useInView: () => ({ ref: vi.fn(), inView: visibility.inView }) }));
const root = '11111111-1111-4111-8111-111111111111';
const base: TaskProgress = { root_task_id: root, revision: 1, status: 'waiting_dependencies',
  completed_steps: 0, total_steps: 1, completed_requirements: 0, total_requirements: 1,
  pending_results: 1, unconfirmed_results: 0,
  steps: [{ id: 'image', title: '生成商品图片', status: 'waiting_result' }],
};
const load = vi.mocked(getTaskProgress);
const tick = async () => { await act(async () => { await Promise.resolve(); }); };

beforeEach(() => { vi.useFakeTimers(); visibility.inView = true; vi.clearAllMocks(); });
afterEach(() => { cleanup(); vi.useRealTimers(); });

describe('authenticated task progress', () => {
  it('does not turn accepted operations into completed output', async () => {
    load.mockResolvedValue(base);
    render(<TaskProgressCard rootTaskId={root} messageId="message" />);
    await tick();
    expect(screen.getByRole('status')).toHaveTextContent('等待任务结果');
    expect(screen.getByText(/已受理，尚未确认最终结果/)).toBeInTheDocument();
    expect(screen.queryByText('全部完成')).not.toBeInTheDocument();
    expect(load).toHaveBeenCalledWith(root, 'message', expect.any(AbortSignal));
  });
  it('refreshes native completion and rechecks a closed root at a slow interval', async () => {
    load.mockResolvedValueOnce(base).mockResolvedValue({ ...base, revision: 2, status: 'completed',
      completed_steps: 1, completed_requirements: 1, pending_results: 0,
      steps: [{ ...base.steps[0], status: 'completed' }] });
    render(<TaskProgressCard rootTaskId={root} messageId="message" />);
    await tick();
    await act(async () => { await vi.advanceTimersByTimeAsync(30_000); });
    expect(screen.getByRole('status')).toHaveTextContent('全部完成');
    await act(async () => { await vi.advanceTimersByTimeAsync(60_000); });
    expect(load).toHaveBeenCalledTimes(3);
  });
  it('clears stale completion when access can no longer be verified', async () => {
    load.mockResolvedValueOnce({ ...base, status: 'completed' }).mockRejectedValueOnce(new Error('revoked'));
    render(<TaskProgressCard rootTaskId={root} messageId="message" />);
    await tick();
    fireEvent.click(screen.getByRole('button', { name: '刷新进度' }));
    await tick();
    expect(screen.getByRole('status')).toHaveTextContent('暂时无法读取任务进度');
    expect(screen.queryByText('全部完成')).not.toBeInTheDocument();
  });
  it('does not query offscreen cards or hidden browser tabs', async () => {
    visibility.inView = false;
    const view = render(<TaskProgressCard rootTaskId={root} messageId="message" />);
    await tick(); expect(load).not.toHaveBeenCalled();
    visibility.inView = true;
    vi.spyOn(document, 'hidden', 'get').mockReturnValue(true);
    view.rerender(<TaskProgressCard rootTaskId={root} messageId="message" />);
    await tick(); expect(load).not.toHaveBeenCalled();
    vi.restoreAllMocks();
  });
  it('ignores an aborted old read when a newer read finishes first', async () => {
    let resolveOld!: (value: TaskProgress) => void;
    load.mockImplementationOnce(() => new Promise(resolve => { resolveOld = resolve; }))
      .mockResolvedValueOnce({ ...base, revision: 2, status: 'completed' });
    render(<TaskProgressCard rootTaskId={root} messageId="message" />);
    await tick();
    fireEvent.click(screen.getByRole('button', { name: '刷新进度' }));
    await tick();
    await act(async () => { resolveOld(base); });
    expect(screen.getByRole('status')).toHaveTextContent('全部完成');
  });
});

it('stops automatic retry after a confirmed denial and clears the previous source on message change', async () => {
  load.mockResolvedValueOnce({ ...base, status: 'completed' }).mockRejectedValueOnce(
    Object.assign(new Error('denied'), { isAxiosError: true, response: { status: 404 } }));
  const view = render(<TaskProgressCard rootTaskId={root} messageId="message" />);
  await tick();
  view.rerender(<TaskProgressCard rootTaskId={root} messageId="another-message" />);
  await tick();
  expect(screen.getByRole('status')).toHaveTextContent('暂时无法读取任务进度');
  expect(screen.queryByText('全部完成')).not.toBeInTheDocument();
  await act(async () => { await vi.advanceTimersByTimeAsync(120_000); });
  expect(load).toHaveBeenCalledTimes(2);
});

it('detects a follow-up after completion and ignores lower-revision completion', async () => {
  load.mockResolvedValueOnce({ ...base, status: 'completed', revision: 2, pending_results: 0 })
    .mockResolvedValueOnce({ ...base, status: 'active', revision: 4 })
    .mockResolvedValueOnce({ ...base, status: 'completed', revision: 3 })
    .mockResolvedValue({ ...base, status: 'active', revision: 5 });
  render(<TaskProgressCard rootTaskId={root} messageId="source" />);
  await tick(); expect(screen.getByRole('status')).toHaveTextContent('全部完成');
  await act(async () => { await vi.advanceTimersByTimeAsync(60_000); });
  expect(screen.getByRole('status')).toHaveTextContent('正在推进');
  await act(async () => { await vi.advanceTimersByTimeAsync(5_000); });
  expect(screen.getByRole('status')).toHaveTextContent('正在推进');
  await act(async () => { await vi.advanceTimersByTimeAsync(5_000); });
  expect(load).toHaveBeenCalledTimes(4);
});

it('uses the original pause entry and preserves accepted work', async () => {
  load.mockResolvedValue(base); vi.mocked(pauseTaskByMessageId).mockResolvedValue();
  render(<TaskProgressCard rootTaskId={root} messageId="source" />); await tick();
  fireEvent.click(screen.getByRole('button', { name: '暂停任务' })); await tick();
  expect(pauseTaskByMessageId).toHaveBeenCalledExactlyOnceWith('source');
  expect(screen.getByText(/已提交暂停请求/)).toBeInTheDocument();
  expect(screen.getByText(/已受理，尚未确认最终结果/)).toBeInTheDocument();
  expect(screen.getByRole('status')).toHaveTextContent('等待任务结果');
});

it('submits resume once and keeps the original text and image visible', async () => {
  load.mockResolvedValue({ ...base, status: 'paused' });
  let done!: (ok: boolean) => void;
  const resume = vi.fn(() => new Promise<boolean>(resolve => { done = resolve; }));
  render(<><p>原规划原文</p><img alt="已有成图" src="/local-existing.png" /><TaskProgressCard rootTaskId={root} messageId="source" onResume={resume} /></>);
  await tick(); const button = screen.getByRole('button', { name: '继续任务' });
  fireEvent.click(button); fireEvent.click(button); await tick();
  expect(resume).toHaveBeenCalledTimes(1); expect(button).toBeDisabled();
  load.mockResolvedValue({ ...base, revision: 2, status: 'waiting_dependencies' });
  await act(async () => { done(true); }); await tick();
  expect(screen.getByText('原规划原文')).toBeInTheDocument();
  expect(screen.getByAltText('已有成图')).toHaveAttribute('src', '/local-existing.png');
  expect(screen.getByText(/继续请求已受理/)).toBeInTheDocument();
  expect(screen.getByRole('status')).toHaveTextContent('等待任务结果');
});
it('stops the logical task through the original entry without removing old output', async () => {
  load.mockResolvedValue(base); vi.mocked(cancelTaskByMessageId).mockResolvedValue();
  render(<><p>原完整规划</p><TaskProgressCard rootTaskId={root} messageId="source" /></>); await tick();
  load.mockResolvedValue({ ...base, revision: 2, status: 'cancelled' });
  fireEvent.click(screen.getByRole('button', { name: '停止任务' })); await tick();
  expect(cancelTaskByMessageId).toHaveBeenCalledExactlyOnceWith('source');
  expect(screen.getByText('原完整规划')).toBeInTheDocument();
  expect(screen.getByRole('status')).toHaveTextContent('已取消');
  expect(screen.queryByRole('button', { name: '继续任务' })).not.toBeInTheDocument();
});

it('shows actual image completion separately from the model working plan', async () => {
  load.mockResolvedValue({ ...base, presentation: 'model_directed',
    image_jobs: { completed: 0, total: 1 },
    working_plan: { explanation: '继续复用原素材', steps: [{ step: '规划方案', status: 'completed' }] } });
  render(<TaskProgressCard rootTaskId={root} messageId="message" presentation="model_directed" />);
  await tick();
  expect(screen.getByText('已有 0 张图片完成')).toBeInTheDocument();
  expect(screen.getByText('工作计划')).toBeInTheDocument();
  expect(screen.queryByText(/项交付要求/)).not.toBeInTheDocument();
  expect(screen.getByRole('status')).toHaveTextContent('等待任务结果');
  expect(screen.getByRole('button', { name: '停止任务' })).toBeInTheDocument();
});

it('does not claim all user goals are satisfied when the current execution closes', async () => {
  load.mockResolvedValue({ ...base, status: 'completed', pending_results: 0, presentation: 'model_directed' });
  render(<TaskProgressCard rootTaskId={root} messageId="message" />);
  await tick();
  expect(screen.getByRole('status')).toHaveTextContent('本轮执行结束');
  expect(screen.queryByText('全部完成')).not.toBeInTheDocument();
});


it('accepts the first real execution after a higher-version plan-only checkpoint', async () => {
  load.mockResolvedValueOnce({ ...base, revision: 9, status: 'active', presentation: 'model_directed', plan_only: true,
    working_plan: { explanation: '', steps: [{ step: '生成报告', status: 'pending' }] } });
  render(<TaskProgressCard rootTaskId={root} messageId="message" />); await tick();
  load.mockResolvedValue({ ...base, revision: 1, presentation: 'model_directed', working_plan: {
    explanation: '', steps: [{ step: '生成报告', status: 'in_progress' }] } });
  fireEvent.click(screen.getByRole('button', { name: '刷新进度' })); await tick();
  expect(screen.getByRole('status')).toHaveTextContent('等待任务结果');
  expect(screen.getByText(/生成报告 · 进行中/)).toBeInTheDocument();
  load.mockResolvedValue({ ...base, revision: 99, status: 'active', presentation: 'model_directed', plan_only: true });
  fireEvent.click(screen.getByRole('button', { name: '刷新进度' })); await tick();
  expect(screen.getByRole('status')).toHaveTextContent('等待任务结果');

});
