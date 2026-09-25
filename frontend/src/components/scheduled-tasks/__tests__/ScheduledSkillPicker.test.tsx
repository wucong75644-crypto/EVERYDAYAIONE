import { act, fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ScheduledSkillPicker } from '../ScheduledSkillPicker';
import { scheduledTaskService } from '../../../services/scheduledTask';
import type { ScheduledSkillChoice } from '../../../types/scheduledTask';

vi.mock('../../../services/scheduledTask', () => ({ scheduledTaskService: { skillOptions: vi.fn() } }));
const v1 = { skill_id: 'orders', revision: 'v1', name: '订单方法' };
const v2 = { ...v1, revision: 'v2', description: '新版方法' };

describe('scheduled Skill selection', () => {
  beforeEach(() => vi.clearAllMocks());
  it('keeps the saved version until the user explicitly replaces it', async () => {
    vi.mocked(scheduledTaskService.skillOptions).mockResolvedValue([v2]);
    const change = vi.fn();
    render(<ScheduledSkillPicker taskId="task" selected={[v1]} onChange={change} />);
    expect(scheduledTaskService.skillOptions).not.toHaveBeenCalled();
    fireEvent.click(screen.getByText('选择固定 Skill'));
    await screen.findByText('订单方法 · v2');
    expect(scheduledTaskService.skillOptions).toHaveBeenCalledWith('task');
    expect(screen.getByText('订单方法 · v1')).toBeVisible();
    expect(change).not.toHaveBeenCalled();
    fireEvent.click(screen.getByText('更换版本'));
    expect(change).toHaveBeenCalledWith([v2]);
  });
  it('allows removing an unavailable binding without discovering another version', () => {
    const change = vi.fn();
    render(<ScheduledSkillPicker selected={[v1]} onChange={change} />);
    fireEvent.click(screen.getByRole('button', { name: '移除 订单方法' }));
    expect(change).toHaveBeenCalledWith([]);
    expect(scheduledTaskService.skillOptions).not.toHaveBeenCalled();
  });
  it('shows failure and lets the user retry without replacing existing choices', async () => {
    vi.mocked(scheduledTaskService.skillOptions).mockRejectedValueOnce(new Error()).mockResolvedValueOnce([]);
    const change = vi.fn();
    render(<ScheduledSkillPicker selected={[v1]} onChange={change} />);
    fireEvent.click(screen.getByText('选择固定 Skill'));
    await screen.findByRole('alert');
    expect(change).not.toHaveBeenCalled();
    fireEvent.click(screen.getByText('选择固定 Skill'));
    await screen.findByText('没有已审核且允许用于此计划任务的 Skill。');
    expect(screen.getByText('订单方法 · v1')).toBeVisible();
  });
  it('ignores a late catalog response after the task changes', async () => {
    let resolve!: (value: ScheduledSkillChoice[]) => void;
    vi.mocked(scheduledTaskService.skillOptions).mockReturnValue(new Promise((r) => { resolve = r; }));
    const view = render(<ScheduledSkillPicker taskId="old" selected={[]} onChange={vi.fn()} />);
    fireEvent.click(screen.getByText('选择固定 Skill'));
    view.rerender(<ScheduledSkillPicker taskId="new" selected={[]} onChange={vi.fn()} />);
    await act(async () => resolve([v2]));
    expect(screen.queryByText('订单方法 · v2')).not.toBeInTheDocument();
  });
  it('limits additions to four while allowing a version replacement', async () => {
    vi.mocked(scheduledTaskService.skillOptions).mockResolvedValue([v2, { skill_id: 'extra', revision: 'v1' }]);
    render(<ScheduledSkillPicker selected={[v1, ...['a', 'b', 'c'].map(skill_id => ({ skill_id, revision: 'v1' }))]} onChange={vi.fn()} />);
    fireEvent.click(screen.getByText('选择固定 Skill'));
    await screen.findByText('更换版本');
    expect(screen.getByText('选择')).toBeDisabled();
    expect(screen.getByText('更换版本')).not.toBeDisabled();
  });
});
