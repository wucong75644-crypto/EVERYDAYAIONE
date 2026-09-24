import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { LazyMotion, domAnimation } from 'framer-motion';
import SessionSkillBindings from '../SessionSkillBindings';
import SkillSelector from '../SkillSelector';
import {
  addSkillBinding, getAvailableSkills, getSkillBindings, removeSkillBinding,
  type SkillBinding, type SkillSummary,
} from '../../../../services/skills';

vi.mock('../../../../services/skills', async (original) => ({
  ...await original<typeof import('../../../../services/skills')>(),
  addSkillBinding: vi.fn(), getAvailableSkills: vi.fn(), getSkillBindings: vi.fn(), removeSkillBinding: vi.fn(),
}));
const skill: SkillSummary = { skill_id: 'orders', revision: 'v2', name: '订单摘要', description: '汇总订单',
  source: 'org', model_selectable: false, triggers: [] };
const binding: SkillBinding = { ...skill, revision: 'v1', binding_id: 'binding-1', available: true };
const panel = (id = 'conv-1', disabled = false) =>
  <SessionSkillBindings key={id} conversationId={id} disabled={disabled} />;

beforeEach(() => {
  vi.mocked(getAvailableSkills).mockResolvedValue([skill]);
  vi.mocked(getSkillBindings).mockResolvedValue([]);
  vi.mocked(addSkillBinding).mockResolvedValue({ binding_id: 'binding-1' });
  vi.mocked(removeSkillBinding).mockResolvedValue(undefined);
});

describe('session Skill bindings', () => {
  it('is available through the existing Skill menu without changing turn selection', async () => {
    const onSelect = vi.fn();
    render(<LazyMotion features={domAnimation}><SkillSelector conversationId="conv-1"
      ensureConversation={async () => 'conv-1'} selected={skill} onSelect={onSelect} disabled={false} /></LazyMotion>);
    fireEvent.click(screen.getByRole('button', { name: 'Skill：订单摘要' }));
    fireEvent.click(screen.getByRole('button', { name: '固定到当前会话' }));
    expect(await screen.findByRole('button', { name: '固定 Skill：订单摘要' })).toBeInTheDocument();
    expect(screen.getByText(/计划任务不继承/)).toBeInTheDocument();
    expect(onSelect).not.toHaveBeenCalled();
  });

  it('adds only after an explicit click and refreshes authoritative state', async () => {
    render(panel());
    const add = await screen.findByRole('button', { name: '固定 Skill：订单摘要' });
    expect(addSkillBinding).not.toHaveBeenCalled();
    vi.mocked(getSkillBindings).mockResolvedValue([{ ...binding, revision: 'v2' }]);
    fireEvent.click(add);
    await waitFor(() => expect(addSkillBinding).toHaveBeenCalledWith('conv-1', skill));
    expect(await screen.findByText('版本已固定')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '固定 Skill：订单摘要' })).not.toBeInTheDocument();
  });

  it('shows the pinned version after newer publication and removes the exact binding', async () => {
    vi.mocked(getSkillBindings).mockResolvedValue([binding]);
    render(panel());
    expect(await screen.findByText('订单摘要 · v1')).toBeInTheDocument();
    expect(screen.queryByText('订单摘要 · v2')).not.toBeInTheDocument();
    vi.mocked(getSkillBindings).mockResolvedValue([]);
    fireEvent.click(screen.getByRole('button', { name: '移除会话 Skill：订单摘要' }));
    await waitFor(() => expect(removeSkillBinding).toHaveBeenCalledWith('conv-1', 'binding-1'));
    expect(await screen.findByRole('button', { name: '固定 Skill：订单摘要' })).toBeInTheDocument();
  });

  it('keeps revoked bindings visible and removable', async () => {
    vi.mocked(getSkillBindings).mockResolvedValue([{ ...binding, available: false }]);
    render(panel());
    expect(await screen.findByText(/当前不可用/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '移除会话 Skill：订单摘要' })).toBeEnabled();
  });

  it('does not optimistically remove a binding when the write fails', async () => {
    vi.mocked(getSkillBindings).mockResolvedValue([binding]);
    vi.mocked(removeSkillBinding).mockRejectedValue(new Error('private SQL'));
    render(panel());
    fireEvent.click(await screen.findByRole('button', { name: '移除会话 Skill：订单摘要' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('修改未成功');
    expect(screen.getByText('订单摘要 · v1')).toBeInTheDocument();
    expect(screen.queryByText(/private SQL/)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '刷新重试' }));
    await waitFor(() => expect(screen.queryByRole('alert')).not.toBeInTheDocument());
  });

  it('clears old state on switching conversations and ignores late responses', async () => {
    let resolve!: (value: SkillBinding[]) => void;
    vi.mocked(getSkillBindings).mockReturnValueOnce(new Promise(r => { resolve = r; })).mockResolvedValue([]);
    const view = render(panel());
    view.rerender(panel('conv-2'));
    expect(await screen.findByText('尚未固定 Skill')).toBeInTheDocument();
    await act(async () => resolve([binding]));
    expect(screen.queryByText('订单摘要 · v1')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '固定 Skill：订单摘要' }));
    await waitFor(() => expect(addSkillBinding).toHaveBeenCalledWith('conv-2', skill));
  });

  it('a late mutation in the previous conversation never refreshes the current one', async () => {
    let resolve!: () => void;
    vi.mocked(removeSkillBinding).mockReturnValueOnce(new Promise(r => { resolve = r; }));
    vi.mocked(getSkillBindings).mockResolvedValueOnce([binding]).mockResolvedValue([]);
    const view = render(panel());
    fireEvent.click(await screen.findByRole('button', { name: '移除会话 Skill：订单摘要' }));
    view.rerender(panel('conv-2'));
    await screen.findByText('尚未固定 Skill');
    const reads = vi.mocked(getSkillBindings).mock.calls.length;
    await act(async () => resolve());
    expect(getSkillBindings).toHaveBeenCalledTimes(reads);
    expect(screen.queryByText('订单摘要 · v1')).not.toBeInTheDocument();
  });

  it('limits additions to four while allowing removal and respects disabled input', async () => {
    vi.mocked(getSkillBindings).mockResolvedValue(Array.from({ length: 4 }, (_, i) =>
      ({ ...binding, skill_id: `fixed-${i}`, name: `固定 ${i}`, binding_id: `binding-${i}` })));
    const view = render(panel());
    expect(await screen.findByRole('button', { name: '固定 Skill：订单摘要' })).toBeDisabled();
    expect(screen.getByRole('button', { name: '移除会话 Skill：固定 0' })).toBeEnabled();
    view.rerender(panel('conv-1', true));
    expect(screen.getByRole('button', { name: '移除会话 Skill：固定 0' })).toBeDisabled();
  });
});
