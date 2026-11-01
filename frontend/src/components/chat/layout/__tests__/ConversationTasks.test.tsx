import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import ConversationTasks from '../ConversationTasks';
import { normalizeMessage } from '../../../../stores/useMessageStore';
import { getTaskProgress, type TaskProgress } from '../../../../services/taskExecution';
import { pauseTaskByMessageId } from '../../../../services/message';

vi.mock('../../../../services/taskExecution', () => ({ getTaskProgress: vi.fn() }));
vi.mock('../../../../services/message', () => ({ pauseTaskByMessageId: vi.fn(), cancelTaskByMessageId: vi.fn() }));
vi.mock('react-intersection-observer', () => ({ useInView: () => ({ ref: vi.fn(), inView: true }) }));
const root = '11111111-1111-4111-8111-111111111111';
const marker = { type: 'task_progress', root_task_id: root, presentation: 'model_directed' };
const message = (id: string, content: unknown, role = 'assistant') => normalizeMessage({
  id, conversation_id: 'c1', role, status: 'completed', content: content as string,
});
const messages = [message('user', [{ type: 'text', text: '读取原表格，不重复操作' }], 'user'), message('first', [marker])];
const base: TaskProgress = { root_task_id: root, revision: 1, status: 'active', presentation: 'model_directed',
  completed_steps: 0, total_steps: 1, completed_requirements: 0, total_requirements: 1,
  pending_results: 0, unconfirmed_results: 0, steps: [],
  working_plan: { explanation: '', steps: [{ step: '读取表格', status: 'completed' }, { step: '统计并总结', status: 'in_progress' }] },
};
beforeEach(() => { vi.clearAllMocks(); vi.mocked(getTaskProgress).mockResolvedValue(base); });
afterEach(() => { cleanup(); vi.useRealTimers(); });

describe('header conversation tasks', () => {
  it('deduplicates follow-ups using the latest assistant source and ignores user markers', async () => {
    render(<ConversationTasks messages={[...messages,
      message('untrusted', [{ ...marker, root_task_id: '22222222-2222-4222-8222-222222222222' }], 'user'),
      message('continued', JSON.stringify([marker]))]} onResume={vi.fn()} />);
    fireEvent.click(screen.getByRole('button', { name: '查看任务' }));
    await screen.findByText('正在推进');
    expect(getTaskProgress).toHaveBeenCalledExactlyOnceWith(root, 'continued', expect.any(AbortSignal));
  });

  it('loads progress only when opened and aborts the original polling on close', async () => {
    render(<ConversationTasks messages={messages} onResume={vi.fn()} />);
    expect(getTaskProgress).not.toHaveBeenCalled();
    expect(screen.queryByText('正在推进')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '查看任务' }));
    expect(await screen.findByText('正在推进')).toBeInTheDocument();
    expect(screen.getByText('读取原表格，不重复操作')).toBeInTheDocument();
    expect(getTaskProgress).toHaveBeenCalledWith(root, 'first', expect.any(AbortSignal));
    const signal = vi.mocked(getTaskProgress).mock.calls[0][2]!;
    fireEvent.click(screen.getByRole('button', { name: '查看任务' }));
    await waitFor(() => expect(screen.queryByText('正在推进')).not.toBeInTheDocument());
    expect(signal.aborted).toBe(true);
  });

  it('retains original message-bound pause and resume controls in the popover', async () => {
    const resume = vi.fn().mockResolvedValue(true);
    const view = render(<ConversationTasks messages={messages} onResume={resume} />);
    fireEvent.click(screen.getByRole('button', { name: '查看任务' }));
    fireEvent.click(await screen.findByRole('button', { name: '暂停任务' }));
    await waitFor(() => expect(pauseTaskByMessageId).toHaveBeenCalledWith('first'));
    vi.mocked(getTaskProgress).mockResolvedValue({ ...base, status: 'paused', revision: 2 });
    fireEvent.click(screen.getByRole('button', { name: '刷新进度' }));
    fireEvent.click(await screen.findByRole('button', { name: '继续任务' }));
    await waitFor(() => expect(resume).toHaveBeenCalledOnce());
    view.unmount();
  });

  it('clears old task controls when switching conversations', async () => {
    const view = render(<ConversationTasks key="c1" messages={messages} onResume={vi.fn()} />);
    fireEvent.click(screen.getByRole('button', { name: '查看任务' }));
    await screen.findByText('正在推进');
    const signal = vi.mocked(getTaskProgress).mock.calls[0][2]!;
    await act(async () => view.rerender(<ConversationTasks key="c2" messages={[]} onResume={vi.fn()} />));
    expect(signal.aborted).toBe(true);
    expect(screen.queryByText('正在推进')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '查看任务' }));
    expect(screen.getByText('暂无工作计划')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '暂停任务' })).not.toBeInTheDocument();
  });
});


it('does not show an old ordinary-code marker as a task or keep polling it', async () => {
  vi.useFakeTimers();
  vi.mocked(getTaskProgress).mockResolvedValue({ ...base, working_plan: undefined });
  render(<ConversationTasks messages={messages} onResume={vi.fn()} />);
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: '查看任务' })); });
  expect(screen.getByText('暂无工作计划')).toBeInTheDocument();
  expect(screen.queryByText('正在推进')).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: '暂停任务' })).not.toBeInTheDocument();
  expect(screen.queryByText('读取原表格，不重复操作')).not.toBeInTheDocument();
  await act(async () => { await vi.advanceTimersByTimeAsync(60_000); });
  expect(getTaskProgress).toHaveBeenCalledTimes(1);
});

it('shows each real plan item by name and refreshes when a saved plan changes', async () => {
  const view=render(<ConversationTasks messages={messages} onResume={vi.fn()} />);
  fireEvent.click(screen.getByRole('button', { name: '查看任务' }));
  expect(await screen.findByText(/读取表格 · 已记录完成/)).toBeInTheDocument();
  expect(screen.getByText(/统计并总结 · 进行中/)).toBeInTheDocument();
  vi.mocked(getTaskProgress).mockResolvedValue({ ...base, revision: 2,
    working_plan: { explanation: '', steps: [{ step: '按新要求输出月度报告', status: 'pending' }] } });
  view.rerender(<ConversationTasks messages={[messages[0],message('first',[{ ...marker,plan_revision:'b'.repeat(64) }])]} onResume={vi.fn()} />);
  expect(await screen.findByText(/按新要求输出月度报告 · 待处理/)).toBeInTheDocument();
  expect(screen.queryByText(/统计并总结 · 进行中/)).not.toBeInTheDocument();
});
