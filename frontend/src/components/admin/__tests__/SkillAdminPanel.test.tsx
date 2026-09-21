import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import SkillAdminPanel from '../SkillAdminPanel';
import * as api from '../../../services/skillAdmin';
import { ApiRequestError } from '../../../services/api';

vi.mock('../../../services/skillAdmin');
const content: api.DraftContent = { description: '按订单生成报告', body: '# 订单报告\n\nReviewed instructions', catalog_metadata: {
  name: '订单报告', triggers: ['订单汇总'], allowed_tool_names: ['file_search'], model_selectable: false,
  conversation_scopes: ['channel'], agent_domains: ['erp'], execution_modes: ['scheduled'], required_permissions: ['orders.read'],
} };
const revision = { revision: 'v-immutable', summary: '旧版报告', status: 'published' as const, catalog_metadata: { name: '旧版名称' }, created_at: '2026-09-19T00:00:00Z' };
function detail(status: api.SkillState = 'draft', approved = false, version = 1): api.SkillDetail {
  return { package_id: 'p1', skill_key: 'orders', scope_kind: 'org', editable: true, available_revision: 'v-immutable',
    draft: { status, version, revision: 'v-next', content: structuredClone(content), approved_by: approved ? 'reviewer' : null,
      approved_at: approved ? '2026-09-20T00:00:00Z' : null, updated_at: '2026-09-20T00:00:00Z' }, revisions: [revision] };
}
const item: api.SkillAdminItem = { package_id: 'p1', skill_key: 'orders', scope_kind: 'org', name: '订单报告', status: 'draft',
  version: 1, published_revision: 'v-immutable', available_revision: 'v-immutable', available_revision_number: 1,
  description: '旧版报告', working_description: '按订单生成报告', approved: false, updated_at: '2026-09-20T00:00:00Z' };
const platformItem: api.SkillAdminItem = { ...item, package_id: 'platform', skill_key: 'platform-key', scope_kind: 'platform', name: '平台说明', status: 'published' };

beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(api.listManagedSkills).mockResolvedValue([item, platformItem]);
  vi.mocked(api.getManagedSkill).mockResolvedValue(detail());
  vi.mocked(api.checkSkillDeletion).mockResolvedValue({ allowed: true, reason: null, blocking_tasks: 0, uncertain_tasks: 0 });
  vi.mocked(api.readSkillRevision).mockResolvedValue({ ...content, body: '# 已发布正文\n\nOriginal immutable content' });
});
async function open(value = detail()) {
  vi.mocked(api.getManagedSkill).mockResolvedValue(value);
  render(<SkillAdminPanel orgId="org-1" />);
  fireEvent.click(await screen.findByRole('button', { name: '打开 订单报告' }));
  await screen.findByRole('button', { name: 'Skill 库' });
}
async function more(label: string) {
  await userEvent.click(screen.getByRole('button', { name: '更多操作' }));
  await userEvent.click(await screen.findByRole('menuitem', { name: label }));
}
async function confirmedPublish() {
  fireEvent.click(screen.getByRole('button', { name: '发布新版本' }));
  fireEvent.click(await screen.findByRole('button', { name: '确认发布' }));
}

describe('Skill admin workspace', () => {
  it('shows attachment summaries from the selected immutable revision', async () => {
    vi.mocked(api.readSkillRevision).mockResolvedValue({ ...content, asset_summaries: [{
      id: 'template', name: '发布模板', kind: 'template', summary: '原版本附件摘要', format: 'md', bytes: 60,
    }] });
    await open(detail('published'));
    expect(await screen.findByText('原版本附件摘要')).toBeInTheDocument();
    expect(screen.getByRole('region', { name: '附件摘要' })).toHaveTextContent('发布模板');
    expect(screen.queryByRole('button', { name: '添加附件' })).not.toBeInTheDocument();
  });

  it('separates scopes, searches names/keys and distinguishes working state from available version', async () => {
    render(<SkillAdminPanel orgId="org-1" />);
    expect(await screen.findByRole('button', { name: '打开 订单报告' })).toHaveTextContent('仍在使用');
    expect(screen.queryByRole('button', { name: '打开 平台说明' })).not.toBeInTheDocument();
    expect(api.getManagedSkill).not.toHaveBeenCalled();
    fireEvent.change(screen.getByLabelText('搜索 Skill'), { target: { value: 'orders' } });
    expect(screen.getByRole('button', { name: '打开 订单报告' })).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('筛选状态'), { target: { value: 'published' } });
    expect(screen.getByText('没有符合条件的 Skill')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /平台 Skill/ }));
    expect(screen.getByRole('button', { name: '打开 平台说明' })).toBeInTheDocument();
  });

  it('creates a named organization draft atomically and then enters editing', async () => {
    vi.mocked(api.createManagedSkill).mockResolvedValue({ package_id: 'p1' });
    render(<SkillAdminPanel orgId="org-1" />);
    fireEvent.click(screen.getByRole('button', { name: '新建 Skill' }));
    fireEvent.change(screen.getByLabelText('名称'), { target: { value: '订单报告' } });
    fireEvent.change(screen.getByLabelText('唯一标识'), { target: { value: 'orders' } });
    fireEvent.click(screen.getByRole('button', { name: '创建草稿' }));
    await screen.findByLabelText('Skill 操作说明');
    expect(api.createManagedSkill).toHaveBeenCalledWith('org-1', 'orders', expect.objectContaining({ catalog_metadata: { name: '订单报告' } }));
    expect(api.saveSkillDraft).not.toHaveBeenCalled();
  });

  it('retains the creation form on a duplicate identifier', async () => {
    vi.mocked(api.createManagedSkill).mockRejectedValue(new ApiRequestError('CONFLICT', 'private', 409));
    render(<SkillAdminPanel orgId="org-1" />);
    fireEvent.click(screen.getByRole('button', { name: '新建 Skill' }));
    fireEvent.change(screen.getByLabelText('名称'), { target: { value: '订单报告' } });
    fireEvent.change(screen.getByLabelText('唯一标识'), { target: { value: 'orders' } });
    fireEvent.click(screen.getByRole('button', { name: '创建草稿' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('该标识已存在');
    expect(screen.getByLabelText('名称')).toHaveValue('订单报告');
  });

  it('saves then submits the returned version, reviews separately and publishes after confirmation', async () => {
    await open();
    const saved = detail('draft', false, 2);
    saved.draft!.content.body = 'Edited draft';
    vi.mocked(api.saveSkillDraft).mockResolvedValue(saved);
    vi.mocked(api.transitionSkill).mockResolvedValue(detail('in_review', false, 3));
    fireEvent.change(screen.getByLabelText('Skill 操作说明'), { target: { value: 'Edited draft' } });
    fireEvent.click(screen.getByRole('button', { name: '保存并提交审核' }));
    await screen.findByRole('button', { name: '审核通过' });
    expect(api.saveSkillDraft).toHaveBeenCalledWith('org-1', 'p1', 1, { ...content, body: 'Edited draft' });
    expect(api.transitionSkill).toHaveBeenCalledWith('org-1', 'p1', 2, 'submit');
    expect(screen.queryByLabelText('Skill 操作说明')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '发布新版本' })).not.toBeInTheDocument();
    vi.mocked(api.transitionSkill).mockResolvedValue(detail('in_review', true, 4));
    fireEvent.click(screen.getByRole('button', { name: '审核通过' }));
    await screen.findByRole('button', { name: '发布新版本' });
    vi.mocked(api.transitionSkill).mockResolvedValue(detail('published', true, 5));
    fireEvent.click(screen.getByRole('button', { name: '发布新版本' }));
    expect(api.transitionSkill).not.toHaveBeenCalledWith('org-1', 'p1', 4, 'publish');
    fireEvent.click(await screen.findByRole('button', { name: '确认发布' }));
    await screen.findByText('发布成功，新版本已可用。');
    expect(api.transitionSkill).toHaveBeenLastCalledWith('org-1', 'p1', 4, 'publish');
    await waitFor(() => expect(screen.getByRole('button', { name: '编辑新版本' })).toBeEnabled());
    await screen.findByText('Original immutable content');
  });

  it('preserves local content and all advanced declarations on a concurrent conflict', async () => {
    await open();
    fireEvent.change(screen.getByLabelText('Skill 操作说明'), { target: { value: 'My unsaved work' } });
    vi.mocked(api.saveSkillDraft).mockRejectedValue(new ApiRequestError('CONFLICT', 'database details', 409));
    fireEvent.click(screen.getByRole('button', { name: '保存草稿' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('未保存的编辑已保留');
    expect(screen.getByLabelText('Skill 操作说明')).toHaveValue('My unsaved work');
    expect(api.saveSkillDraft).toHaveBeenCalledWith('org-1', 'p1', 1, { ...content, body: 'My unsaved work' });
    expect(screen.queryByText(/database details/)).not.toBeInTheDocument();
  });

  it('reports saved-but-not-submitted accurately and retries without saving again', async () => {
    await open();
    vi.mocked(api.saveSkillDraft).mockResolvedValue(detail('draft', false, 2));
    vi.mocked(api.transitionSkill).mockRejectedValueOnce(new ApiRequestError('OFFLINE', 'private', 503)).mockResolvedValueOnce(detail('in_review'));
    fireEvent.change(screen.getByLabelText('用途说明'), { target: { value: '新用途' } });
    fireEvent.click(screen.getByRole('button', { name: '保存并提交审核' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('草稿已保存，但提交审核失败');
    fireEvent.click(screen.getByRole('button', { name: '提交审核' }));
    await screen.findByRole('button', { name: '审核通过' });
    expect(api.saveSkillDraft).toHaveBeenCalledTimes(1);
    expect(api.transitionSkill).toHaveBeenLastCalledWith('org-1', 'p1', 2, 'submit');
  });

  it('retains approval after NAS failure and retries inside the publication dialog', async () => {
    await open(detail('in_review', true));
    vi.mocked(api.transitionSkill).mockRejectedValueOnce(new ApiRequestError('NAS', '/private/path', 503)).mockResolvedValueOnce(detail('published', true));
    await confirmedPublish();
    expect(await screen.findByRole('alert')).toHaveTextContent('受控存储暂不可用');
    expect(screen.queryByText('/private/path')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '确认发布' }));
    await screen.findByText('发布成功，新版本已可用。');
    expect(api.transitionSkill).toHaveBeenCalledTimes(2);
  });

  it('guards leaving and refresh, preserves input if refresh fails, and protects browser unload', async () => {
    await open();
    fireEvent.change(screen.getByLabelText('Skill 操作说明'), { target: { value: 'Keep this' } });
    const event = new Event('beforeunload', { cancelable: true });
    window.dispatchEvent(event);
    expect(event.defaultPrevented).toBe(true);
    fireEvent.click(screen.getByRole('button', { name: 'Skill 库' }));
    expect(await screen.findByRole('dialog')).toHaveTextContent('放弃未保存的修改');
    fireEvent.click(screen.getByRole('button', { name: '继续编辑' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    expect(screen.getByLabelText('Skill 操作说明')).toHaveValue('Keep this');
    await more('刷新详情');
    vi.mocked(api.getManagedSkill).mockRejectedValueOnce(new Error('offline'));
    fireEvent.click(await screen.findByRole('button', { name: '放弃修改并继续' }));
    await screen.findByRole('alert');
    expect(screen.getByLabelText('Skill 操作说明')).toHaveValue('Keep this');
    fireEvent.click(screen.getByRole('button', { name: 'Skill 库' }));
    fireEvent.click(await screen.findByRole('button', { name: '放弃修改并继续' }));
    await screen.findByRole('button', { name: '打开 订单报告' });
    const cleanEvent = new Event('beforeunload', { cancelable: true }); window.dispatchEvent(cleanEvent);
    expect(cleanEvent.defaultPrevented).toBe(false);
  });

  it('keeps unsaved editing across preview and historical reads, including repeated selection', async () => {
    await open();
    fireEvent.change(screen.getByLabelText('Skill 操作说明'), { target: { value: '# Local draft\n\nMy pending text' } });
    fireEvent.click(screen.getByRole('button', { name: '预览', exact: true }));
    expect(screen.getByText('My pending text')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /版本历史/ }));
    fireEvent.click(screen.getByRole('button', { name: '查看第 1 版' }));
    await screen.findByText('Original immutable content');
    fireEvent.click(screen.getByRole('button', { name: '查看第 1 版' }));
    expect(screen.getByText('Original immutable content')).toBeInTheDocument();
    expect(api.readSkillRevision).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole('button', { name: '编辑草稿' }));
    expect(screen.getByLabelText('Skill 操作说明')).toHaveValue('# Local draft\n\nMy pending text');
    fireEvent.click(screen.getByRole('button', { name: '编辑草稿' }));
    expect(screen.getByLabelText('Skill 操作说明')).toHaveValue('# Local draft\n\nMy pending text');
  });

  it('requires confirmation for deprecation and then offers only safe deletion and read-only views', async () => {
    await open(detail('published'));
    await more('废弃 Skill');
    expect(screen.getByRole('dialog')).toHaveTextContent('已经激活的任务仍可使用原版本恢复');
    expect(api.transitionSkill).not.toHaveBeenCalled();
    vi.mocked(api.transitionSkill).mockResolvedValue({ ...detail('deprecated'), available_revision: null });
    fireEvent.click(screen.getByRole('button', { name: '确认废弃' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    expect(screen.queryByRole('button', { name: '编辑新版本' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '更多操作' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '解除停用' })).not.toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole('button', { name: '删除 Skill' })).toBeEnabled());
  });

  it('shows a primary reenable action after stopping and restores the original version after confirmation', async () => {
    await open(detail('published', true, 5));
    vi.mocked(api.transitionSkill).mockResolvedValue({ ...detail('disabled', true, 6), available_revision: null,
      revisions: [{ ...revision, status: 'disabled' }] });
    await more('停用 Skill');
    expect(screen.getByRole('dialog')).toHaveTextContent('可在详情页解除停用');
    fireEvent.click(screen.getByRole('button', { name: '确认停用' }));
    fireEvent.click(await screen.findByRole('button', { name: '解除停用' }));
    expect(screen.getByRole('dialog')).toHaveTextContent('将恢复停用前的状态和原有版本');
    expect(api.transitionSkill).not.toHaveBeenCalledWith('org-1', 'p1', 6, 'enable');
    vi.mocked(api.transitionSkill).mockResolvedValue(detail('published', true, 7));
    fireEvent.click(screen.getByRole('button', { name: '确认解除停用' }));
    await screen.findByText('已解除停用，当前状态：已发布。');
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    expect(api.transitionSkill).toHaveBeenLastCalledWith('org-1', 'p1', 6, 'enable');
    expect(screen.queryByRole('button', { name: '解除停用' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: '编辑新版本' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /版本历史/ })).toHaveTextContent('1');
    expect(screen.getByText('当前可用版本').nextElementSibling).toHaveTextContent('第 1 版');
  });

  it('can directly deprecate a stopped Skill with an explicit warning about old task recovery', async () => {
    const deprecated = { ...detail('deprecated', true, 15), available_revision: null,
      revisions: [{ ...revision, status: 'deprecated' as const }] };
    vi.mocked(api.listManagedSkills).mockResolvedValue([{ ...item, status: 'deprecated', available_revision: null }]);
    await open({ ...detail('disabled', true, 14), available_revision: null });
    await more('废弃 Skill');
    expect(screen.getByRole('dialog')).toHaveTextContent('重新允许此前已激活的旧任务恢复');
    expect(api.transitionSkill).not.toHaveBeenCalled();
    vi.mocked(api.transitionSkill).mockResolvedValueOnce(deprecated);
    fireEvent.click(screen.getByRole('button', { name: '确认废弃' }));
    await screen.findByText('已废弃，新的使用已停止，已有任务仍可恢复。');
    expect(api.transitionSkill).toHaveBeenLastCalledWith('org-1', 'p1', 14, 'deprecate');
    expect(screen.getByText('已废弃')).toBeInTheDocument();
    for (const name of ['更多操作', '解除停用', '编辑新版本', '发布新版本']) {
      expect(screen.queryByRole('button', { name })).not.toBeInTheDocument();
    }
    await waitFor(() => expect(screen.getByRole('button', { name: '删除 Skill' })).toBeEnabled());
    fireEvent.click(screen.getByRole('button', { name: /版本历史/ }));
    fireEvent.click(screen.getByRole('button', { name: '查看第 1 版' }));
    await screen.findByText('Original immutable content');
    fireEvent.click(screen.getByRole('button', { name: 'Skill 库' }));
    expect(await screen.findByRole('button', { name: '打开 订单报告' })).toHaveTextContent('已废弃');
  });

  it('restores a legacy stopped deprecated Skill to a read-only deprecated state', async () => {
    await open({ ...detail('disabled', true, 14), available_revision: null });
    fireEvent.click(screen.getByRole('button', { name: '解除停用' }));
    vi.mocked(api.transitionSkill).mockResolvedValue({ ...detail('deprecated', true, 15), available_revision: null });
    fireEvent.click(screen.getByRole('button', { name: '确认解除停用' }));
    await screen.findByText('已解除停用，当前状态：已废弃。仍禁止新的使用，已有任务可恢复。');
    expect(screen.queryByRole('button', { name: '更多操作' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '解除停用' })).not.toBeInTheDocument();
  });

  it('removes a safe deprecated Skill after confirmation and returns to the refreshed library', async () => {
    await open({ ...detail('deprecated', true, 15), available_revision: null });
    await waitFor(() => expect(screen.getByRole('button', { name: '删除 Skill' })).toBeEnabled());
    fireEvent.click(screen.getByRole('button', { name: '删除 Skill' }));
    expect(screen.getByRole('dialog')).toHaveTextContent('历史版本、NAS 正文和审计记录仍保留');
    expect(api.deleteManagedSkill).not.toHaveBeenCalled();
    vi.mocked(api.deleteManagedSkill).mockResolvedValue({ package_id: 'p1', deleted: true });
    vi.mocked(api.listManagedSkills).mockResolvedValue([]);
    fireEvent.click(screen.getByRole('button', { name: '确认删除' }));
    await screen.findByText('已从 Skill 库删除，历史版本和审计记录仍保留。');
    expect(api.deleteManagedSkill).toHaveBeenCalledWith('org-1', 'p1', 15);
    expect(screen.queryByRole('button', { name: '打开 订单报告' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Skill 库' })).not.toBeInTheDocument();
  });

  it.each([
    [{ allowed: false, reason: 'SKILL_DELETE_IN_USE', blocking_tasks: 2, uncertain_tasks: 0 }, '有 2 个未结束的任务引用此 Skill'],
    [{ allowed: false, reason: 'SKILL_DELETE_CHECK_UNCERTAIN', blocking_tasks: 0, uncertain_tasks: 1 }, '有 1 个任务尚不能确认是否仍依赖此 Skill'],
  ])('disables deletion for blocking or unknown tasks', async (check, message) => {
    vi.mocked(api.checkSkillDeletion).mockResolvedValue(check as api.SkillDeletionCheck);
    await open(detail('deprecated'));
    await screen.findByText(new RegExp(message as string));
    expect(screen.getByRole('button', { name: '删除 Skill' })).toBeDisabled();
    expect(api.deleteManagedSkill).not.toHaveBeenCalled();
    vi.mocked(api.checkSkillDeletion).mockResolvedValue({ allowed: true, reason: null, blocking_tasks: 0, uncertain_tasks: 0 });
    fireEvent.click(screen.getByRole('button', { name: '重新检查' }));
    await waitFor(() => expect(screen.getByRole('button', { name: '删除 Skill' })).toBeEnabled());
  });

  it('fails closed when safety checks fail and preserves the detail on a final dependency conflict', async () => {
    vi.mocked(api.checkSkillDeletion).mockRejectedValueOnce(new Error('private database path'));
    await open(detail('deprecated'));
    await screen.findByText('安全检查暂不可用，请重新检查。');
    expect(screen.getByRole('button', { name: '删除 Skill' })).toBeDisabled();
    expect(screen.queryByText('private database path')).not.toBeInTheDocument();
    vi.mocked(api.checkSkillDeletion).mockResolvedValue({ allowed: true, reason: null, blocking_tasks: 0, uncertain_tasks: 0 });
    fireEvent.click(screen.getByRole('button', { name: '重新检查' }));
    await waitFor(() => expect(screen.getByRole('button', { name: '删除 Skill' })).toBeEnabled());
    fireEvent.click(screen.getByRole('button', { name: '删除 Skill' }));
    vi.mocked(api.deleteManagedSkill).mockRejectedValue(new ApiRequestError('CONFLICT', 'private', 409));
    fireEvent.click(screen.getByRole('button', { name: '确认删除' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('状态或任务依赖已变化');
    expect(screen.getByRole('dialog')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '取消' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    expect(screen.getByRole('button', { name: 'Skill 库' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '重新检查' })).toBeInTheDocument();
    expect(screen.queryByText('已从 Skill 库删除，历史版本和审计记录仍保留。')).not.toBeInTheDocument();
  });

  it('does not offer deletion or check dependencies of a platform Skill', async () => {
    await open({ ...detail('deprecated'), editable: false, scope_kind: 'platform', draft: null,
      revisions: [{ ...revision, status: 'deprecated' }] });
    expect(screen.queryByRole('button', { name: '删除 Skill' })).not.toBeInTheDocument();
    expect(api.checkSkillDeletion).not.toHaveBeenCalled();
  });

  it.each([
    ['draft', false, '草稿'], ['in_review', false, '待审核'], ['in_review', true, '审核通过'],
  ] as const)('reports the actual restored working state %s (approved=%s)', async (status, approved, label) => {
    await open({ ...detail('disabled', approved, 6), available_revision: null });
    fireEvent.click(screen.getByRole('button', { name: '解除停用' }));
    vi.mocked(api.transitionSkill).mockResolvedValue({ ...detail(status, approved, 7), available_revision: null });
    fireEvent.click(screen.getByRole('button', { name: '确认解除停用' }));
    await screen.findByText(`已解除停用，当前状态：${label}。`);
    expect(screen.getByText('当前可用版本').nextElementSibling).toHaveTextContent('尚未启用');
  });

  it('keeps a stopped Skill and the confirmation open when reenable fails', async () => {
    await open({ ...detail('disabled', true, 6), available_revision: null });
    vi.mocked(api.transitionSkill).mockRejectedValue(new ApiRequestError('STORAGE', 'private path', 503));
    fireEvent.click(screen.getByRole('button', { name: '解除停用' }));
    fireEvent.click(screen.getByRole('button', { name: '确认解除停用' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Skill 服务或受控存储暂不可用');
    expect(screen.getByRole('dialog')).toBeInTheDocument();
    expect(screen.queryByText('已解除停用，当前状态：已发布。')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '编辑新版本' })).not.toBeInTheDocument();
  });

  it.each(['deprecated', 'published'] as const)('does not offer reenable for %s', async status => {
    await open(detail(status));
    expect(screen.queryByRole('button', { name: '解除停用' })).not.toBeInTheDocument();
  });

  it('does not let organization admins reenable a platform Skill', async () => {
    await open({ ...detail('disabled'), scope_kind: 'platform', editable: false, draft: null,
      revisions: [{ ...revision, status: 'disabled' }] });
    expect(screen.queryByRole('button', { name: '解除停用' })).not.toBeInTheDocument();
  });

  it('loads platform body by default, escapes HTML and prevents automatic external images', async () => {
    vi.mocked(api.getManagedSkill).mockResolvedValue({ ...detail(), package_id: 'platform', scope_kind: 'platform', editable: false, draft: null });
    vi.mocked(api.readSkillRevision).mockResolvedValue({ ...content, body: '<script>untrusted()</script>\n\n![external](https://example.invalid/track.png)\n\n[bad](javascript:alert(1))' });
    render(<SkillAdminPanel orgId="org-1" />);
    await screen.findByRole('button', { name: '打开 订单报告' });
    fireEvent.click(screen.getByRole('button', { name: /平台 Skill/ }));
    fireEvent.click(screen.getByRole('button', { name: '打开 平台说明' }));
    await screen.findByText('<script>untrusted()</script>');
    expect(document.querySelector('script')).toBeNull(); expect(document.querySelector('img')).toBeNull();
    expect(document.querySelector('a[href^="javascript:"]')).toBeNull();
    expect(api.readSkillRevision).toHaveBeenCalledWith('org-1', 'platform', 'v-immutable');
    expect(screen.queryByRole('button', { name: '编辑新版本' })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: '更多操作' }));
    expect(screen.queryByRole('menuitem', { name: '废弃 Skill' })).not.toBeInTheDocument();
  });

  it('does not display unverified published body when reading fails and supports retry', async () => {
    vi.mocked(api.readSkillRevision).mockRejectedValueOnce(new ApiRequestError('HASH', 'secret hash', 503)).mockResolvedValueOnce(content);
    await open(detail('published'));
    await screen.findByRole('button', { name: '重试读取正文' });
    expect(screen.queryByText('Reviewed instructions')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '重试读取正文' }));
    await screen.findByText('Reviewed instructions');
  });

  it('ignores late detail and revision results after switching organization', async () => {
    let resolveDetail!: (value: api.SkillDetail) => void;
    vi.mocked(api.getManagedSkill).mockReturnValue(new Promise(r => { resolveDetail = r; }));
    const view = render(<SkillAdminPanel orgId="org-1" />);
    fireEvent.click(await screen.findByRole('button', { name: '打开 订单报告' }));
    vi.mocked(api.listManagedSkills).mockResolvedValue([]);
    view.rerender(<SkillAdminPanel orgId="org-2" />);
    await act(async () => resolveDetail(detail()));
    await screen.findByText('还没有组织 Skill');
    expect(screen.queryByLabelText('Skill 操作说明')).not.toBeInTheDocument();
    expect(api.readSkillRevision).not.toHaveBeenCalled();
    view.unmount();
    let resolveRead!: (value: api.DraftContent) => void;
    vi.mocked(api.listManagedSkills).mockResolvedValue([item]);
    vi.mocked(api.getManagedSkill).mockResolvedValue(detail('published'));
    vi.mocked(api.readSkillRevision).mockReturnValue(new Promise(r => { resolveRead = r; }));
    const next = render(<SkillAdminPanel orgId="org-1" />);
    fireEvent.click(await screen.findByRole('button', { name: '打开 订单报告' }));
    await screen.findByText('加载版本正文…');
    vi.mocked(api.listManagedSkills).mockResolvedValue([]);
    next.rerender(<SkillAdminPanel orgId="org-2" />);
    await act(async () => resolveRead(content));
    await screen.findByText('还没有组织 Skill');
    expect(screen.queryByText('Reviewed instructions')).not.toBeInTheDocument();
  });

  it('prevents duplicate writes and cancels the chained submit after organization changes', async () => {
    let resolve!: (value: api.SkillDetail) => void;
    vi.mocked(api.saveSkillDraft).mockReturnValue(new Promise(r => { resolve = r; }));
    const onState = vi.fn();
    const view = render(<SkillAdminPanel orgId="org-1" onNavigationStateChange={onState} />);
    fireEvent.click(await screen.findByRole('button', { name: '打开 订单报告' }));
    fireEvent.change(await screen.findByLabelText('Skill 操作说明'), { target: { value: 'Updated draft' } });
    fireEvent.click(screen.getByRole('button', { name: '保存并提交审核' }));
    fireEvent.click(screen.getByRole('button', { name: '保存并提交审核' }));
    expect(api.saveSkillDraft).toHaveBeenCalledTimes(1);
    expect(onState).toHaveBeenLastCalledWith({ dirty: true, busy: true });
    vi.mocked(api.listManagedSkills).mockResolvedValue([]);
    view.rerender(<SkillAdminPanel orgId="org-2" onNavigationStateChange={onState} />);
    await act(async () => resolve(detail('draft', false, 2)));
    await screen.findByText('还没有组织 Skill');
    expect(api.transitionSkill).not.toHaveBeenCalled();
  });

  it('edits advanced fields without JSON and validates required content before review', async () => {
    await open();
    fireEvent.click(screen.getByText('高级设置'));
    fireEvent.change(screen.getByLabelText(/触发提示/), { target: { value: '新提示\n另一个提示\n' } });
    fireEvent.click(screen.getByLabelText(/允许模型选择/));
    const saved = detail('draft', false, 2); vi.mocked(api.saveSkillDraft).mockResolvedValue(saved);
    fireEvent.click(screen.getByRole('button', { name: '保存草稿' }));
    await screen.findByText('草稿已保存。');
    expect(api.saveSkillDraft).toHaveBeenCalledWith('org-1', 'p1', 1, { ...content, catalog_metadata: { ...content.catalog_metadata, triggers: ['新提示', '另一个提示'], model_selectable: true } });
    fireEvent.change(screen.getByLabelText('用途说明'), { target: { value: ' ' } });
    fireEvent.click(screen.getByRole('button', { name: '保存并提交审核' }));
    expect(screen.getByRole('alert')).toHaveTextContent('请先填写用途说明和操作说明');
    expect(api.transitionSkill).not.toHaveBeenCalled();
  });

  it('shows a recoverable load error instead of falsely reporting an empty library', async () => {
    vi.mocked(api.listManagedSkills).mockRejectedValueOnce(new Error('offline')).mockResolvedValueOnce([item]);
    render(<SkillAdminPanel orgId="org-1" />);
    await screen.findByText('Skill 列表暂时无法加载');
    expect(screen.queryByText('还没有组织 Skill')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '刷新列表' }));
    await screen.findByRole('button', { name: '打开 订单报告' });
  });

  it('does not call an inactive or deprecated revision available', async () => {
    vi.mocked(api.listManagedSkills).mockResolvedValue([{ ...item, status: 'deprecated', available_revision: null }]);
    render(<SkillAdminPanel orgId="org-1" />);
    const row = await screen.findByRole('button', { name: '打开 订单报告' });
    expect(within(row).getAllByText('已停止使用').length).toBeGreaterThan(0);
    expect(within(row).queryByText('第 1 版')).not.toBeInTheDocument();
  });
});
