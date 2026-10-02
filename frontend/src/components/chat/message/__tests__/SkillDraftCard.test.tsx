import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { ChangeSet } from '../../../../types/changeset';
import { changeSetService } from '../../../../services/changeSet';
import { skillCreationService } from '../../../../services/skillCreation';
import SkillDraftCard from '../SkillDraftCard';

vi.mock('../../../../services/changeSet', () => ({
  changeSetService: {
    get: vi.fn(), confirm: vi.fn(), cancel: vi.fn(),
  },
}));

vi.mock('../../../../services/skillCreation', () => ({
  skillCreationService: {
    revise: vi.fn(), estimateTrial: vi.fn(), runTrial: vi.fn(),
    listTrials: vi.fn(), feedback: vi.fn(),
  },
}));

function makeChangeSet(overrides: Partial<ChangeSet> = {}): ChangeSet {
  return {
    id: 'change-1', org_id: 'org-1', resource_type: 'skill_draft', resource_id: 'package-1',
    operation: 'create', base_revision: '0', base_snapshot: {},
    proposed_snapshot: {
      name: '商品白底图', description: '按规则生成白底商品图',
      task_modes: ['image-i2i'], triggers: ['做商品白底图'], input_requirements: ['商品参考图'],
      content_sha256: 'a'.repeat(64),
      content: { description: '按规则生成白底商品图', body: '保留产品结构，生成纯白背景。',
        catalog_metadata: { name: '商品白底图', task_modes: ['image-i2i'] } },
    },
    patch: [], diff: {}, risk_level: 'low', policy_snapshot: { requires_approval: true },
    plan_snapshot: null, status: 'awaiting_approval', idempotency_key: 'key-1',
    expires_at: '2026-10-02T00:00:00Z', created_by: 'user-1', created_by_type: 'user',
    audit_subject: {}, revision: 5, created_at: '2026-10-01T10:00:00Z',
    updated_at: '2026-10-01T10:00:00Z', checks: [],
    ...overrides,
  };
}

describe('SkillDraftCard', () => {
  beforeEach(() => {
    vi.mocked(changeSetService.get).mockResolvedValue(makeChangeSet());
    vi.mocked(changeSetService.confirm).mockResolvedValue(makeChangeSet({ status: 'applied' }));
    vi.mocked(changeSetService.cancel).mockResolvedValue(makeChangeSet({ status: 'cancelled' }));
    vi.mocked(skillCreationService.listTrials).mockResolvedValue({ enabled: true, runs: [] });
    vi.mocked(skillCreationService.estimateTrial).mockResolvedValue({
      text_to_image: { model_id: 'text-image', image_count: 1, estimated_credits: 3 },
      image_to_image: { model_id: 'image-image', image_count: 1, estimated_credits: 8 },
      reference_images: [],
    });
    vi.stubGlobal('crypto', { randomUUID: () => 'trial-request-1' });
  });

  afterEach(() => {
    vi.clearAllMocks();
    vi.unstubAllGlobals();
  });

  it('confirms the exact preview revision and content hash to create only a draft', async () => {
    render(<SkillDraftCard changeSetId="change-1" />);
    expect(await screen.findByText('核对整理结果，确认后只创建草稿。')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '创建草稿' }));

    await waitFor(() => expect(changeSetService.confirm).toHaveBeenCalledWith('change-1', {
      expected_change_set_revision: 5,
      content_sha256: 'a'.repeat(64),
    }));
    expect(await screen.findByText('草稿已创建，后续可提交审核发布。')).toBeInTheDocument();
  });

  it('shows the existing Skill target before confirming a draft update', async () => {
    const existing = makeChangeSet({
      operation: 'update',
      proposed_snapshot: {
        ...makeChangeSet().proposed_snapshot,
        target_skill: { name: '商品图流程', skill_key: 'product-image', expected_version: 4 },
      },
      base_revision: '4',
    });
    vi.mocked(changeSetService.get).mockResolvedValue(existing);
    render(<SkillDraftCard changeSetId="change-1" />);

    expect(await screen.findByText('核对更新内容，确认后只更新现有草稿。')).toBeInTheDocument();
    expect(screen.getByText('商品图流程 · 当前草稿版本 v4')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '更新现有草稿' })).toBeInTheDocument();
  });

  it('keeps draft creation available while hiding trials when the trial flag is off', async () => {
    vi.mocked(skillCreationService.listTrials).mockResolvedValue({ enabled: false, runs: [] });
    render(<SkillDraftCard changeSetId="change-1" />);

    expect(await screen.findByRole('button', { name: '创建草稿' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '文本试用' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '图片试用' })).not.toBeInTheDocument();
  });

  it('runs an isolated text trial and stores helpful feedback without confirming the draft', async () => {
    vi.mocked(skillCreationService.runTrial).mockResolvedValue({
      trial_id: 'trial-1', candidate_revision: 5, content_sha256: 'a'.repeat(64),
      mode: 'text', output: '提示词符合要求。',
      model_id: 'test-model', replayed: false,
    });
    render(<SkillDraftCard changeSetId="change-1" />);
    fireEvent.click(await screen.findByRole('button', { name: '文本试用' }));
    fireEvent.change(await screen.findByLabelText('输入一组实际任务资料'), {
      target: { value: '一只白色陶瓷杯，保留杯身图案' },
    });
    fireEvent.click(screen.getByRole('button', { name: '开始试用' }));

    expect(await screen.findByText('提示词符合要求。')).toBeInTheDocument();
    expect(skillCreationService.runTrial).toHaveBeenCalledWith('change-1', expect.objectContaining({
      mode: 'text', input_text: '一只白色陶瓷杯，保留杯身图案',
      idempotency_key: 'trial-request-1', expected_revision: 5,
    }));
    fireEvent.click(screen.getByRole('button', { name: '试用结果有帮助' }));
    await waitFor(() => expect(skillCreationService.feedback).toHaveBeenCalledWith('trial-1', 'helpful'));
    expect(changeSetService.confirm).not.toHaveBeenCalled();
  });
});
