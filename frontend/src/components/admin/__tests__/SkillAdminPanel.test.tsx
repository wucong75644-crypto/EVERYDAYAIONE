import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import SkillAdminPanel from '../SkillAdminPanel';
import * as api from '../../../services/skillAdmin';
import { ApiRequestError } from '../../../services/api';

vi.mock('../../../services/skillAdmin');
const content = { description: '按订单生成报告', body: 'Reviewed instructions', catalog_metadata: { name: '订单报告' } };
function detail(status: api.SkillState = 'draft', approved = false): api.SkillDetail {
  return { package_id: 'p1', skill_key: 'orders', scope_kind: 'org', editable: true,
    draft: { status, version: 1, revision: 'v-next', content, approved_by: approved ? 'reviewer' : null,
      approved_at: approved ? '2026-09-20T00:00:00Z' : null, updated_at: '2026-09-20T00:00:00Z' },
    revisions: [] };
}
const item: api.SkillAdminItem = { package_id: 'p1', skill_key: 'orders', scope_kind: 'org', status: 'draft',
  version: 1, published_revision: null, description: null, approved: false };

beforeEach(() => {
  vi.mocked(api.listManagedSkills).mockResolvedValue([item]);
  vi.mocked(api.getManagedSkill).mockResolvedValue(detail());
});
async function open() {
  render(<SkillAdminPanel orgId="org-1" />);
  fireEvent.click(await screen.findByRole('button', { name: /orders.*组织/ }));
  await screen.findByLabelText('Skill 正文（Markdown）');
}

describe('Skill admin workflow', () => {
  it('saves draft only, requires approval before offering publish, then publishes exact version', async () => {
    await open();
    fireEvent.change(screen.getByLabelText('Skill 正文（Markdown）'), { target: { value: 'Edited draft' } });
    expect(screen.getByRole('button', { name: '提交审核' })).toBeDisabled();
    expect(screen.getByRole('button', { name: /orders.*组织/ })).toBeDisabled();
    vi.mocked(api.saveSkillDraft).mockResolvedValue(detail());
    fireEvent.click(screen.getByRole('button', { name: '保存草稿' }));
    await waitFor(() => expect(api.saveSkillDraft).toHaveBeenCalledWith('org-1', 'p1', 1,
      { ...content, body: 'Edited draft' }));
    await screen.findByText('草稿已保存。');
    vi.mocked(api.transitionSkill).mockResolvedValue(detail('in_review'));
    fireEvent.click(screen.getByRole('button', { name: '提交审核' }));
    await screen.findByRole('button', { name: '审核通过' });
    expect(screen.getByLabelText('Skill 正文（Markdown）')).toBeDisabled();
    expect(screen.queryByRole('button', { name: '发布到本组织' })).not.toBeInTheDocument();
    vi.mocked(api.transitionSkill).mockResolvedValue(detail('in_review', true));
    fireEvent.click(screen.getByRole('button', { name: '审核通过' }));
    await screen.findByRole('button', { name: '发布到本组织' });
    vi.mocked(api.transitionSkill).mockResolvedValue(detail('published', true));
    fireEvent.click(screen.getByRole('button', { name: '发布到本组织' }));
    await screen.findByText('发布成功，新 Turn 将使用此版本。');
    expect(api.transitionSkill).toHaveBeenLastCalledWith('org-1', 'p1', 1, 'publish');
    expect(screen.getByRole('button', { name: '创建修订草稿' })).toBeEnabled();
  });

  it('preserves unsaved content on a concurrent edit conflict', async () => {
    await open();
    fireEvent.change(screen.getByLabelText('Skill 正文（Markdown）'), { target: { value: 'My unsaved work' } });
    vi.mocked(api.saveSkillDraft).mockRejectedValue(new ApiRequestError('CONFLICT', 'database details', 409));
    fireEvent.click(screen.getByRole('button', { name: '保存草稿' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('未保存的编辑已保留');
    expect(screen.getByLabelText('Skill 正文（Markdown）')).toHaveValue('My unsaved work');
    expect(screen.queryByText(/database details/)).not.toBeInTheDocument();
  });

  it('retains approved content after NAS failure and permits retry', async () => {
    vi.mocked(api.getManagedSkill).mockResolvedValue(detail('in_review', true));
    await open();
    vi.mocked(api.transitionSkill).mockRejectedValueOnce(new ApiRequestError('NAS', '/private/path', 503))
      .mockResolvedValueOnce(detail('published', true));
    fireEvent.click(screen.getByRole('button', { name: '发布到本组织' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('受控存储暂不可用');
    expect(screen.getByLabelText('Skill 正文（Markdown）')).toHaveValue(content.body);
    fireEvent.click(screen.getByRole('button', { name: '发布到本组织' }));
    await screen.findByText('发布成功，新 Turn 将使用此版本。');
  });

  it('requires local confirmation and explains deprecated vs disabled replay behavior', async () => {
    vi.mocked(api.getManagedSkill).mockResolvedValue(detail('published'));
    await open();
    fireEvent.click(screen.getByRole('button', { name: '废弃 Skill' }));
    expect(screen.getByRole('alert')).toHaveTextContent('已激活的 Actor 仍可恢复原版本');
    expect(api.transitionSkill).not.toHaveBeenCalled();
    vi.mocked(api.transitionSkill).mockResolvedValue(detail('deprecated'));
    fireEvent.click(screen.getByRole('button', { name: '确认废弃' }));
    await waitFor(() => expect(screen.queryByRole('button', { name: '确认废弃' })).not.toBeInTheDocument());
    expect(screen.queryByRole('button', { name: '创建修订草稿' })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '禁用 Skill' }));
    expect(screen.getByRole('alert')).toHaveTextContent('Actor 恢复均被阻止');
  });

  it('renders platform history as text and exposes no mutation actions', async () => {
    const platform: api.SkillDetail = { ...detail(), scope_kind: 'platform', editable: false, draft: null,
      revisions: [{ revision: 'v1', summary: 'platform', status: 'published', catalog_metadata: {}, created_at: '2026-09-20T00:00:00Z' }] };
    vi.mocked(api.getManagedSkill).mockResolvedValue(platform);
    vi.mocked(api.readSkillRevision).mockResolvedValue({ ...content, body: '<script>untrusted()</script>' });
    render(<SkillAdminPanel orgId="org-1" />);
    fireEvent.click(await screen.findByRole('button', { name: /orders.*组织/ }));
    fireEvent.click(await screen.findByRole('button', { name: '查看 v1' }));
    await screen.findByText('<script>untrusted()</script>');
    expect(document.querySelector('script')).toBeNull();
    expect(screen.queryByRole('button', { name: '废弃 Skill' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '创建修订草稿' })).not.toBeInTheDocument();
  });

  it('ignores late results after switching organization', async () => {
    let resolve!: (value: api.SkillDetail) => void;
    vi.mocked(api.getManagedSkill).mockReturnValue(new Promise(r => { resolve = r; }));
    const view = render(<SkillAdminPanel orgId="org-1" />);
    fireEvent.click(await screen.findByRole('button', { name: /orders.*组织/ }));
    vi.mocked(api.listManagedSkills).mockResolvedValue([]);
    view.rerender(<SkillAdminPanel orgId="org-2" />);
    await act(async () => resolve(detail()));
    await screen.findByText('暂无 Skill，可创建组织草稿。');
    expect(screen.queryByLabelText('Skill 正文（Markdown）')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: '刷新列表' })).toBeEnabled();
  });

  it('rejects malformed metadata without sending a write', async () => {
    await open();
    fireEvent.change(screen.getByLabelText('目录声明（JSON）'), { target: { value: '[invalid' } });
    fireEvent.click(screen.getByRole('button', { name: '保存草稿' }));
    expect(screen.getByRole('alert')).toHaveTextContent('有效 JSON 对象');
    expect(api.saveSkillDraft).not.toHaveBeenCalled();
  });
});
