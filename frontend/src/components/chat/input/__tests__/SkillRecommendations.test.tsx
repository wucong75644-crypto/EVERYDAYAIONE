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
function details() { fireEvent.click(screen.getByRole('button', { name: 'Skill 详情：PDF 摘要' })); }
function filters() { fireEvent.click(screen.getByRole('button', { name: '文件类型' })); }
beforeEach(() => {
  vi.mocked(getAvailableSkills).mockResolvedValue([skill]);
  vi.mocked(getSkillRecommendations).mockResolvedValue(batch);
  vi.mocked(sendSkillRecommendationFeedback).mockResolvedValue(undefined);
});
afterEach(() => vi.unstubAllEnvs());

describe('quick Skill catalog', () => {
  it('shows a single summary and explains recommendations in details without automatic selection', async () => {
    const p = props();
    render(<SkillRecommendations {...p} />);
    expect(screen.getAllByText('PDF 摘要')).toHaveLength(1);
    expect(screen.queryByText(/匹配所选文件类型/)).not.toBeInTheDocument();
    details();
    expect(await screen.findByText('匹配所选文件类型：PDF')).toBeInTheDocument();
    expect(p.onSelect).not.toHaveBeenCalled();
    expect(sendSkillRecommendationFeedback).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: '选择，用于本条消息' }));
    expect(p.onSelect).toHaveBeenCalledWith(skill);
    expect(sendSkillRecommendationFeedback).toHaveBeenCalledWith('conv-1', 'rec-1', batch.candidates[0], 'selected');
  });
  it('uses catalog metadata even when a recommendation contains a wrong name or description', async () => {
    vi.mocked(getSkillRecommendations).mockResolvedValue({ ...batch, candidates: [{ ...batch.candidates[0], name: '错误名称', description: '不可信说明' }] });
    render(<SkillRecommendations {...props()} />);
    details();
    await screen.findByText('匹配所选文件类型：PDF');
    expect(screen.queryByText(/错误名称|不可信说明/)).not.toBeInTheDocument();
  });
  it('shows logical dependency status and blocks activation while a required capability is unavailable', async () => {
    const dependent = { ...skill, capability_status: [
      { capability: 'crm.customer.read', required: true, available: false },
    ] };
    const p = props();
    render(<SkillRecommendations {...p} skills={[dependent]} />);
    expect(screen.getByText('依赖能力不可用')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '选择 Skill：PDF 摘要' })).toBeDisabled();
    details();
    expect(await screen.findByText('crm.customer.read · 必需 · 依赖能力不可用')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '依赖能力不可用' })).toBeDisabled();
    expect(screen.queryByText(/Bearer|https?:|token|内部地址/i)).not.toBeInTheDocument();
    expect(p.onSelect).not.toHaveBeenCalled();
  });
  it('sends only an explicitly chosen file type and execution mode', async () => {
    render(<SkillRecommendations {...props()} permissionMode="plan" />);
    await waitFor(() => expect(getSkillRecommendations).toHaveBeenCalledWith('conv-1', [], 'plan'));
    filters();
    fireEvent.change(screen.getByRole('combobox', { name: '推荐文件类型' }), { target: { value: 'pdf' } });
    await waitFor(() => expect(getSkillRecommendations).toHaveBeenLastCalledWith('conv-1', ['pdf'], 'plan'));
  });
  it('isolates unknown and stale identities without removing ordinary selection', async () => {
    vi.mocked(getSkillRecommendations).mockResolvedValue({ ...batch, candidates: [
      { ...batch.candidates[0], skill_id: 'foreign', name: '其他组织 Skill' }, { ...batch.candidates[0], revision: 'v2' },
    ] });
    const p = props();
    render(<SkillRecommendations {...p} />);
    filters();
    expect(await screen.findByText('暂无匹配建议，仍可自行选择。')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '完成' }));
    expect(screen.queryByText('其他组织 Skill')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '选择 Skill：PDF 摘要' }));
    expect(p.onSelect).toHaveBeenCalledWith(skill);
    expect(sendSkillRecommendationFeedback).not.toHaveBeenCalled();
  });
  it('never offers a disabled or unauthorized Skill absent from the catalog', async () => {
    render(<SkillRecommendations {...props()} skills={[]} />);
    expect(screen.getByText('当前会话暂无可用 Skill')).toBeInTheDocument();
    await waitFor(() => expect(getSkillRecommendations).toHaveBeenCalled());
    expect(screen.queryByRole('button', { name: '选择 Skill：PDF 摘要' })).not.toBeInTheDocument();
  });
  it('records not relevant without choosing a Skill or removing normal availability', async () => {
    const p = props();
    render(<SkillRecommendations {...p} />);
    details();
    fireEvent.click(await screen.findByRole('button', { name: '不相关：PDF 摘要' }));
    expect(sendSkillRecommendationFeedback).toHaveBeenCalledWith('conv-1', 'rec-1', batch.candidates[0], 'not_relevant');
    expect(p.onSelect).not.toHaveBeenCalled();
    expect(screen.queryByText(/匹配所选文件类型/)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '返回 Skill 列表' }));
    expect(screen.getByRole('button', { name: '选择 Skill：PDF 摘要' })).toBeEnabled();
  });
  it('keeps ordinary selection available after recommendation or feedback failures', async () => {
    vi.mocked(getSkillRecommendations).mockRejectedValueOnce(new Error('private'));
    const view = render(<SkillRecommendations {...props()} />);
    filters();
    expect(await screen.findByText('建议暂不可用，仍可自行选择 Skill。')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '完成' }));
    expect(screen.getByRole('button', { name: '选择 Skill：PDF 摘要' })).toBeEnabled();
    view.unmount();
    vi.mocked(sendSkillRecommendationFeedback).mockRejectedValue(new Error('failed'));
    const p = props();
    render(<SkillRecommendations {...p} />);
    details();
    await screen.findByText('匹配所选文件类型：PDF');
    fireEvent.click(screen.getByRole('button', { name: '选择，用于本条消息' }));
    expect(p.onSelect).toHaveBeenCalledWith(skill);
    expect(await screen.findByText('反馈未保存，不影响选择。')).toBeInTheDocument();
  });
  it('ignores late recommendations after context changes', async () => {
    let resolve!: (value: SkillRecommendationBatch) => void;
    vi.mocked(getSkillRecommendations).mockReturnValueOnce(new Promise(r => { resolve = r; })).mockResolvedValueOnce({ ...batch, candidates: [] });
    const p = props();
    const view = render(<SkillRecommendations key="conv-1" {...p} />);
    view.rerender(<SkillRecommendations key="conv-2" {...p} conversationId="conv-2" />);
    details();
    await act(async () => resolve(batch));
    expect(screen.queryByText(/匹配所选文件类型/)).not.toBeInTheDocument();
  });
  it('hides recommendation controls when the server flag is disabled, even with stale candidates', async () => {
    vi.mocked(getSkillRecommendations).mockResolvedValue({ status: 'disabled', recommendation_id: null, candidates: batch.candidates });
    render(<SkillRecommendations {...props()} />);
    await waitFor(() => expect(screen.queryByRole('button', { name: '文件类型' })).not.toBeInTheDocument());
    expect(screen.getByRole('button', { name: '选择 Skill：PDF 摘要' })).toBeInTheDocument();
  });
  it('makes no recommendation requests with the UI flag off', async () => {
    vi.stubEnv('VITE_SKILL_RECOMMENDATIONS_ENABLED', 'false');
    render(<LazyMotion features={domAnimation}><SkillSelector conversationId="conv-1" ensureConversation={vi.fn()}
      selected={null} onSelect={vi.fn()} disabled={false} /></LazyMotion>);
    fireEvent.click(screen.getByRole('button', { name: '选择 Skill' }));
    await screen.findByText('PDF 摘要');
    expect(getSkillRecommendations).not.toHaveBeenCalled();
    expect(screen.queryByRole('button', { name: '文件类型' })).not.toBeInTheDocument();
  });
  it('one click uses the existing selector and never offers a duplicate item', async () => {
    vi.stubEnv('VITE_SKILL_RECOMMENDATIONS_ENABLED', 'true');
    const onSelect = vi.fn();
    render(<LazyMotion features={domAnimation}><SkillSelector conversationId="conv-1" ensureConversation={vi.fn()}
      selected={null} onSelect={onSelect} disabled={false} /></LazyMotion>);
    fireEvent.click(screen.getByRole('button', { name: '选择 Skill' }));
    const choice = await screen.findByRole('button', { name: '选择 Skill：PDF 摘要' });
    expect(screen.getAllByRole('button', { name: '选择 Skill：PDF 摘要' })).toHaveLength(1);
    await act(async () => { fireEvent.click(choice); });
    expect(onSelect).toHaveBeenCalledWith(skill, 'conv-1');
  });
  it('keeps a pinned older version distinct from the latest catalog version', async () => {
    const p = props();
    await act(async () => { render(<SkillRecommendations {...p} bindings={[{ ...skill, revision: 'v0', binding_id: 'fixed', available: true }]} />); });
    expect(screen.getByRole('button', { name: '选择 Skill：PDF 摘要' })).toBeDisabled();
    details();
    expect(screen.getByText(/当前会话已固定 v0/)).toBeInTheDocument();
    expect(p.onSelect).not.toHaveBeenCalled();
  });
});
