import { useState } from 'react';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { LazyMotion, domAnimation } from 'framer-motion';
import SessionSkillBindings from '../SessionSkillBindings';
import { useSkillBindings } from '../useSkillBindings';
import { addSkillBinding, getAvailableSkills, getSkillBindings, removeSkillBinding,
  type SkillBinding, type SkillSummary } from '../../../../services/skills';

vi.mock('../../../../services/skills', async (original) => ({
  ...await original<typeof import('../../../../services/skills')>(),
  addSkillBinding: vi.fn(), getAvailableSkills: vi.fn(), getSkillBindings: vi.fn(), removeSkillBinding: vi.fn(),
}));
const skill: SkillSummary = { skill_id: 'orders', revision: 'v2', name: '订单摘要', description: '汇总订单',
  source: 'org', model_selectable: false, triggers: [] };
const binding: SkillBinding = { ...skill, binding_id: 'binding-1', available: true };
const selectionChanged = vi.fn();
function Panel({ id = 'conv-1', disabled = false, enabled = true, draft = null }: {
  id?: string; disabled?: boolean; enabled?: boolean; draft?: SkillSummary | null;
}) {
  const state = useSkillBindings(id, enabled);
  const [selected, setSelected] = useState(draft);
  return <LazyMotion features={domAnimation}>
    {enabled && <SessionSkillBindings key={id} selected={selected} state={state} disabled={disabled}
      onSelect={value => { selectionChanged(value); setSelected(value); }} onComplete={vi.fn()} />}
    <button disabled={state.blocked} onClick={() => setSelected(null)}>发送</button>
  </LazyMotion>;
}
async function scope() {
  await waitFor(() => expect(getSkillBindings).toHaveBeenCalled());
  fireEvent.click(await screen.findByRole('button', { name: /^使用范围/ }));
}
beforeEach(() => {
  vi.mocked(getAvailableSkills).mockResolvedValue([skill]);
  vi.mocked(getSkillBindings).mockResolvedValue([]);
  vi.mocked(addSkillBinding).mockResolvedValue({ binding_id: 'binding-1' });
  vi.mocked(removeSkillBinding).mockResolvedValue(undefined);
});

describe('Skill tags and explicit scope changes', () => {
  it('pins only on explicit scope selection and retains the binding after send', async () => {
    render(<Panel draft={skill} />);
    await scope();
    expect(addSkillBinding).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: /固定到当前会话/ }));
    await screen.findByRole('group', { name: '会话固定的 Skill' });
    expect(addSkillBinding).toHaveBeenCalledExactlyOnceWith('conv-1', skill);
    expect(selectionChanged).toHaveBeenLastCalledWith(null);
    expect(screen.queryByRole('group', { name: '已选择的 Skill' })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '发送' }));
    expect(screen.getByRole('group', { name: '会话固定的 Skill' })).toBeInTheDocument();
  });
  it('converts a current pinned revision to one turn only after removal succeeds', async () => {
    vi.mocked(getSkillBindings).mockResolvedValue([binding]);
    render(<Panel />);
    await scope();
    fireEvent.click(screen.getByRole('button', { name: /仅本条消息/ }));
    await screen.findByRole('group', { name: '已选择的 Skill' });
    expect(removeSkillBinding).toHaveBeenCalledExactlyOnceWith('conv-1', 'binding-1');
    expect(selectionChanged).toHaveBeenCalledWith(binding);
  });
  it('does not silently replace an older pinned revision with the latest one', async () => {
    vi.mocked(getSkillBindings).mockResolvedValue([{ ...binding, revision: 'v1' }]);
    render(<Panel />);
    await scope();
    expect(screen.getByText('使用范围 · v1')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /仅本条消息/ }));
    expect(await screen.findByRole('alert')).toHaveTextContent('版本或权限可能已变化');
    expect(removeSkillBinding).not.toHaveBeenCalled();
    expect(selectionChanged).not.toHaveBeenCalled();
    // A failed preflight is not an ambiguous write and must not invent a turn selection.
    vi.mocked(getSkillBindings).mockResolvedValue([]);
    fireEvent.click(screen.getByRole('button', { name: '刷新重试' }));
    await waitFor(() => expect(screen.queryByRole('alert')).not.toBeInTheDocument());
    expect(selectionChanged).not.toHaveBeenCalled();
  });
  it('keeps revoked bindings visible and removable', async () => {
    vi.mocked(getSkillBindings).mockResolvedValue([{ ...binding, available: false }]);
    render(<Panel />);
    expect(await screen.findByText('（不可用）')).toBeInTheDocument();
    await scope();
    expect(screen.getByRole('button', { name: /仅本条消息/ })).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: '移除会话 Skill：订单摘要' }));
    await waitFor(() => expect(screen.queryByRole('group', { name: '会话固定的 Skill' })).not.toBeInTheDocument());
  });
  it('preserves selection after a failed write and holds send until state is refreshed', async () => {
    vi.mocked(addSkillBinding).mockRejectedValue(new Error('private SQL'));
    render(<Panel draft={skill} />);
    await scope();
    fireEvent.click(screen.getByRole('button', { name: /固定到当前会话/ }));
    expect(await screen.findByRole('alert')).toHaveTextContent('使用范围尚未确认');
    expect(screen.getByRole('group', { name: '已选择的 Skill' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '发送' })).toBeDisabled();
    expect(screen.queryByText(/private SQL/)).not.toBeInTheDocument();
    expect(selectionChanged).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: '刷新重试' }));
    await waitFor(() => expect(screen.queryByRole('alert')).not.toBeInTheDocument());
    expect(screen.getByRole('button', { name: '发送' })).toBeEnabled();
    expect(selectionChanged).not.toHaveBeenCalled();
  });
  it('reconciles a timed-out pin that actually succeeded without duplicating the tag', async () => {
    vi.mocked(addSkillBinding).mockRejectedValue(new Error('timeout'));
    render(<Panel draft={skill} />);
    await scope();
    fireEvent.click(screen.getByRole('button', { name: /固定到当前会话/ }));
    await screen.findByRole('alert');
    vi.mocked(getSkillBindings).mockResolvedValue([binding]);
    fireEvent.click(screen.getByRole('button', { name: '刷新重试' }));
    await screen.findByRole('group', { name: '会话固定的 Skill' });
    expect(screen.queryByRole('group', { name: '已选择的 Skill' })).not.toBeInTheDocument();
    expect(selectionChanged).toHaveBeenCalledExactlyOnceWith(null);
    expect(screen.getByRole('button', { name: '发送' })).toBeEnabled();
  });
  it('restores a turn selection after a timed-out unpin is confirmed by refresh', async () => {
    vi.mocked(getSkillBindings).mockResolvedValue([binding]);
    vi.mocked(removeSkillBinding).mockRejectedValue(new Error('timeout'));
    render(<Panel />);
    await scope();
    fireEvent.click(screen.getByRole('button', { name: /仅本条消息/ }));
    await screen.findByRole('alert');
    vi.mocked(getSkillBindings).mockResolvedValue([]);
    fireEvent.click(screen.getByRole('button', { name: '刷新重试' }));
    await screen.findByRole('group', { name: '已选择的 Skill' });
    expect(selectionChanged).toHaveBeenCalledExactlyOnceWith(binding);
  });
  it('never applies a late mutation to the next conversation', async () => {
    let resolve!: () => void;
    vi.mocked(getSkillBindings).mockResolvedValueOnce([binding]).mockResolvedValue([]);
    vi.mocked(removeSkillBinding).mockReturnValueOnce(new Promise(r => { resolve = r; }));
    const view = render(<Panel />);
    fireEvent.click(await screen.findByRole('button', { name: '移除会话 Skill：订单摘要' }));
    view.rerender(<Panel id="conv-2" />);
    await act(async () => resolve());
    expect(screen.queryByText('订单摘要')).not.toBeInTheDocument();
    expect(selectionChanged).not.toHaveBeenCalled();
  });
  it('does not issue the remove after a conversation change during revision validation', async () => {
    let resolve!: (value: SkillSummary[]) => void;
    vi.mocked(getSkillBindings).mockResolvedValueOnce([binding]).mockResolvedValue([]);
    vi.mocked(getAvailableSkills).mockReturnValueOnce(new Promise(r => { resolve = r; }));
    const view = render(<Panel />);
    await scope();
    fireEvent.click(screen.getByRole('button', { name: /仅本条消息/ }));
    view.rerender(<Panel id="conv-2" />);
    await act(async () => resolve([skill]));
    expect(removeSkillBinding).not.toHaveBeenCalled();
  });
  it('ignores a late binding read after switching conversations', async () => {
    let resolve!: (value: SkillBinding[]) => void;
    vi.mocked(getSkillBindings).mockReturnValueOnce(new Promise(r => { resolve = r; })).mockResolvedValue([]);
    const view = render(<Panel />);
    await waitFor(() => expect(getSkillBindings).toHaveBeenCalledWith('conv-1'));
    view.rerender(<Panel id="conv-2" />);
    await act(async () => resolve([binding]));
    expect(screen.queryByText('订单摘要')).not.toBeInTheDocument();
  });
  it('limits pins to four while allowing removal; disabled input prevents writes', async () => {
    vi.mocked(getSkillBindings).mockResolvedValue(Array.from({ length: 4 }, (_, i) =>
      ({ ...binding, skill_id: `fixed-${i}`, name: `固定 ${i}`, binding_id: `binding-${i}` })));
    const view = render(<Panel draft={skill} />);
    await screen.findByRole('button', { name: '移除会话 Skill：固定 0' });
    fireEvent.click(screen.getByRole('button', { name: '使用范围：订单摘要，仅本条' }));
    expect(screen.getByRole('button', { name: /固定到当前会话/ })).toBeDisabled();
    expect(screen.getByRole('button', { name: '移除会话 Skill：固定 0' })).toBeEnabled();
    await act(async () => { view.rerender(<Panel draft={skill} disabled />); });
    expect(screen.getByRole('button', { name: '移除会话 Skill：固定 0' })).toBeDisabled();
  });
  it('makes no binding requests when Skill UI is disabled', async () => {
    render(<Panel enabled={false} />);
    await act(async () => {});
    expect(getSkillBindings).not.toHaveBeenCalled();
  });
});
