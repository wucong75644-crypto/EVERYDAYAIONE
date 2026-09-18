import { act, fireEvent, render, renderHook, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { LazyMotion, domAnimation } from 'framer-motion';
import SkillSelector from '../SkillSelector';
import { useTurnSkillSelection } from '../useTurnSkillSelection';
import { getAvailableSkills, type SkillSummary } from '../../../../services/skills';
import { isSkillUiEnabled } from '../../../../config/featureFlags';

vi.mock('../../../../services/skills', async (importOriginal) => ({
  ...await importOriginal<typeof import('../../../../services/skills')>(),
  getAvailableSkills: vi.fn(),
}));

const skill: SkillSummary = {
  skill_id: 'orders', name: '订单摘要', revision: 'v2', description: '按既定格式汇总订单',
  triggers: [], source: 'org', model_selectable: false,
};
const wrap = (props: React.ComponentProps<typeof SkillSelector>) =>
  <LazyMotion features={domAnimation}><SkillSelector {...props} /></LazyMotion>;
const props = () => ({
  conversationId: 'conv-1', ensureConversation: vi.fn(async () => 'conv-new'),
  selected: null, onSelect: vi.fn(), disabled: false,
});
afterEach(() => vi.unstubAllEnvs());

describe('Skill selection', () => {
  it('shows only server summaries including manual-only skills and selects one', async () => {
    vi.mocked(getAvailableSkills).mockResolvedValue([skill]);
    const p = props();
    render(wrap(p));
    fireEvent.click(screen.getByRole('button', { name: '选择 Skill' }));
    fireEvent.click(await screen.findByRole('button', { name: /订单摘要 · v2/ }));
    expect(getAvailableSkills).toHaveBeenCalledExactlyOnceWith('conv-1');
    expect(p.onSelect).toHaveBeenCalledWith(skill, 'conv-1');
    expect(p.ensureConversation).not.toHaveBeenCalled();
  });

  it('shows empty catalog without offering invisible skills', async () => {
    vi.mocked(getAvailableSkills).mockResolvedValue([]);
    render(wrap(props()));
    fireEvent.click(screen.getByRole('button', { name: '选择 Skill' }));
    expect(await screen.findByText('当前会话暂无可用 Skill')).toBeInTheDocument();
    expect(screen.queryByText(/订单摘要/)).not.toBeInTheDocument();
  });

  it('shows safe fetch failure and allows refresh', async () => {
    vi.mocked(getAvailableSkills).mockRejectedValueOnce(new Error('/private/nas SQL policy'))
      .mockResolvedValueOnce([skill]);
    render(wrap(props()));
    fireEvent.click(screen.getByRole('button', { name: '选择 Skill' }));
    expect(await screen.findByText('暂时无法获取 Skill，请重试。')).toBeInTheDocument();
    expect(screen.queryByText(/private/)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '重试' }));
    expect(await screen.findByText('订单摘要 · v2')).toBeInTheDocument();
  });

  it('creates a scoped conversation before discovery for the first message', async () => {
    vi.mocked(getAvailableSkills).mockResolvedValue([skill]);
    const p = { ...props(), conversationId: null };
    const view = render(wrap(p));
    fireEvent.click(screen.getByRole('button', { name: '选择 Skill' }));
    await waitFor(() => expect(getAvailableSkills).toHaveBeenCalledWith('conv-new'));
    view.rerender(wrap({ ...p, conversationId: 'conv-new' }));
    fireEvent.click(await screen.findByRole('button', { name: /订单摘要 · v2/ }));
    expect(p.onSelect).toHaveBeenCalledWith(skill, 'conv-new');
  });

  it('ignores late catalog results from another conversation', async () => {
    let resolve!: (value: SkillSummary[]) => void;
    vi.mocked(getAvailableSkills).mockReturnValue(new Promise(r => { resolve = r; }));
    const p = props();
    const view = render(wrap(p));
    fireEvent.click(screen.getByRole('button', { name: '选择 Skill' }));
    view.rerender(wrap({ ...p, conversationId: 'conv-other' }));
    await act(async () => resolve([skill]));
    expect(screen.queryByText(/订单摘要/)).not.toBeInTheDocument();
  });

  it('reuses an in-flight conversation creation when the menu is reopened', async () => {
    let resolve!: (value: string) => void;
    const ensureConversation = vi.fn(() => new Promise<string>(r => { resolve = r; }));
    vi.mocked(getAvailableSkills).mockResolvedValue([]);
    render(wrap({ ...props(), conversationId: null, ensureConversation }));
    const trigger = screen.getByRole('button', { name: '选择 Skill' });
    fireEvent.click(trigger);
    fireEvent.click(trigger);
    fireEvent.click(trigger);
    expect(ensureConversation).toHaveBeenCalledOnce();
    await act(async () => resolve('conv-new'));
    expect(getAvailableSkills).toHaveBeenCalledWith('conv-new');
  });

  it('disables selection during a running turn', () => {
    render(wrap({ ...props(), disabled: true }));
    expect(screen.getByRole('button', { name: '选择 Skill' })).toBeDisabled();
    expect(getAvailableSkills).not.toHaveBeenCalled();
  });

  it('consumes only the identity for one send and clears on conversation or mode changes', () => {
    const { result, rerender } = renderHook(({ id, enabled }) => useTurnSkillSelection(id, enabled), {
      initialProps: { id: 'conv-1', enabled: true },
    });
    act(() => result.current.select(skill, 'conv-1'));
    expect(result.current.selected).toEqual(skill);
    act(() => expect(result.current.take()).toEqual({ skill_id: 'orders', revision: 'v2' }));
    expect(result.current.selected).toBeNull();
    act(() => expect(result.current.take()).toBeUndefined());
    act(() => result.current.select(skill, 'conv-1'));
    rerender({ id: 'conv-2', enabled: true });
    expect(result.current.selected).toBeNull();
    rerender({ id: 'conv-1', enabled: true });
    expect(result.current.selected).toBeNull();
    act(() => result.current.select(skill, 'conv-1'));
    rerender({ id: 'conv-1', enabled: false });
    act(() => expect(result.current.take()).toBeUndefined());
    rerender({ id: 'conv-1', enabled: true });
    expect(result.current.selected).toBeNull();
  });

  it('requires explicit opt-in for the independent UI flag', () => {
    vi.stubEnv('VITE_SKILL_UI_ENABLED', '');
    expect(isSkillUiEnabled()).toBe(false);
    vi.stubEnv('VITE_SKILL_UI_ENABLED', 'false');
    expect(isSkillUiEnabled()).toBe(false);
    vi.stubEnv('VITE_SKILL_UI_ENABLED', 'true');
    expect(isSkillUiEnabled()).toBe(true);
  });
});
