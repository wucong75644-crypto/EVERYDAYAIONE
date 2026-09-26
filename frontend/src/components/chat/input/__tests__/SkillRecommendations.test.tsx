import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { LazyMotion, domAnimation } from 'framer-motion';
import SkillRecommendations from '../SkillRecommendations';
import SkillSelector from '../SkillSelector';
import { getAvailableSkills, getSkillRecommendations, sendSkillRecommendationFeedback,
  type SkillSummary, type SkillRecommendationBatch } from '../../../../services/skills';

vi.mock('../../../../services/skills', async (original) => ({
  ...await original<typeof import('../../../../services/skills')>(),
  getAvailableSkills: vi.fn(), getSkillRecommendations: vi.fn(), sendSkillRecommendationFeedback: vi.fn(),
}));
const skill: SkillSummary = { skill_id: 'pdf', name: 'PDF 摘要', revision: 'v1', description: '摘要',
  triggers: [], source: 'org', model_selectable: false };
const batch: SkillRecommendationBatch = { status: 'ready', recommendation_id: 'rec-1', candidates: [
  { ...skill, reasons: [{ code: 'file_type', values: ['pdf'] }] },
] };
const props = () => ({ conversationId: 'conv-1', skills: [skill], disabled: false, onSelect: vi.fn(), permissionMode: 'auto' as const });
beforeEach(() => {
  vi.mocked(getAvailableSkills).mockResolvedValue([skill]);
  vi.mocked(getSkillRecommendations).mockResolvedValue(batch);
  vi.mocked(sendSkillRecommendationFeedback).mockResolvedValue(undefined);
});
afterEach(() => vi.unstubAllEnvs());

describe('Skill recommendations', () => {
  it('explains suggestions without selecting until the user clicks', async () => {
    const p = props();
    render(<SkillRecommendations {...p} />);
    expect(await screen.findByText('匹配所选文件类型：pdf')).toBeInTheDocument();
    expect(p.onSelect).not.toHaveBeenCalled();
    expect(sendSkillRecommendationFeedback).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: '选择建议：PDF 摘要' }));
    expect(p.onSelect).toHaveBeenCalledWith(skill);
    expect(sendSkillRecommendationFeedback).toHaveBeenCalledWith('conv-1', 'rec-1', batch.candidates[0], 'selected');
  });
  it('sends only an explicitly selected file type and current mode', async () => {
    render(<SkillRecommendations {...props()} permissionMode="plan" />);
    await screen.findByText('匹配所选文件类型：pdf');
    fireEvent.change(screen.getByRole('combobox', { name: '推荐文件类型' }), { target: { value: 'pdf' } });
    await waitFor(() => expect(getSkillRecommendations).toHaveBeenLastCalledWith('conv-1', ['pdf'], 'plan'));
  });
  it('isolates unknown or stale recommendation identities from selection', async () => {
    vi.mocked(getSkillRecommendations).mockResolvedValue({ ...batch, candidates: [
      { ...batch.candidates[0], skill_id: 'foreign' }, { ...batch.candidates[0], revision: 'v2' },
    ] });
    render(<SkillRecommendations {...props()} />);
    expect(await screen.findByText('暂无建议，可自行选择 Skill。')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /选择建议/ })).not.toBeInTheDocument();
  });
  it('records not-relevant without choosing or changing bindings', async () => {
    const p = props();
    render(<SkillRecommendations {...p} />);
    fireEvent.click(await screen.findByRole('button', { name: '不相关：PDF 摘要' }));
    expect(sendSkillRecommendationFeedback).toHaveBeenCalledWith('conv-1', 'rec-1', batch.candidates[0], 'not_relevant');
    expect(p.onSelect).not.toHaveBeenCalled();
    expect(screen.queryByRole('button', { name: /选择建议/ })).not.toBeInTheDocument();
  });
  it('keeps ordinary selection available when recommendation or feedback fails', async () => {
    vi.mocked(getSkillRecommendations).mockRejectedValueOnce(new Error('private'));
    const view = render(<SkillRecommendations {...props()} />);
    expect(await screen.findByText('建议暂不可用，可从全部 Skill 中选择。')).toBeInTheDocument();
    view.unmount();
    vi.mocked(sendSkillRecommendationFeedback).mockRejectedValue(new Error('failed'));
    const p = props();
    render(<SkillRecommendations {...p} />);
    fireEvent.click(await screen.findByRole('button', { name: '选择建议：PDF 摘要' }));
    expect(p.onSelect).toHaveBeenCalledWith(skill);
    expect(await screen.findByText('反馈未保存，不影响选择。')).toBeInTheDocument();
  });
  it('ignores late results after context changes', async () => {
    let resolve!: (value: SkillRecommendationBatch) => void;
    vi.mocked(getSkillRecommendations).mockReturnValueOnce(new Promise(r => { resolve = r; }))
      .mockResolvedValueOnce({ ...batch, candidates: [] });
    const p = props();
    const view = render(<SkillRecommendations key="conv-1" {...p} />);
    view.rerender(<SkillRecommendations key="conv-2" {...p} conversationId="conv-2" />);
    await act(async () => resolve(batch));
    expect(screen.queryByRole('button', { name: /选择建议/ })).not.toBeInTheDocument();
  });
  it('hides suggestions when the server flag is disabled', async () => {
    vi.mocked(getSkillRecommendations).mockResolvedValue({ status: 'disabled', recommendation_id: null, candidates: [] });
    const view = render(<SkillRecommendations {...props()} />);
    await waitFor(() => expect(view.container).toBeEmptyDOMElement());
  });
  it('makes no recommendation request with the UI flag off', async () => {
    vi.stubEnv('VITE_SKILL_RECOMMENDATIONS_ENABLED', 'false');
    render(<LazyMotion features={domAnimation}><SkillSelector conversationId="conv-1" ensureConversation={vi.fn()}
      selected={null} onSelect={vi.fn()} disabled={false} /></LazyMotion>);
    fireEvent.click(screen.getByRole('button', { name: '选择 Skill' }));
    await screen.findByText('PDF 摘要 · v1');
    expect(getSkillRecommendations).not.toHaveBeenCalled();
  });
  it('one click uses the existing turn selector when enabled', async () => {
    vi.stubEnv('VITE_SKILL_RECOMMENDATIONS_ENABLED', 'true');
    const onSelect = vi.fn();
    render(<LazyMotion features={domAnimation}><SkillSelector conversationId="conv-1" ensureConversation={vi.fn()}
      selected={null} onSelect={onSelect} disabled={false} /></LazyMotion>);
    fireEvent.click(screen.getByRole('button', { name: '选择 Skill' }));
    const suggestion = await screen.findByRole('button', { name: '选择建议：PDF 摘要' });
    await act(async () => { fireEvent.click(suggestion); });
    expect(onSelect).toHaveBeenCalledWith(skill, 'conv-1');
  });
});
