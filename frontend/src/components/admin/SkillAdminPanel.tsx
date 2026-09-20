import { useEffect, useRef, useState } from 'react';
import { ApiRequestError } from '../../services/api';
import {
  createManagedSkill, getManagedSkill, listManagedSkills, readSkillRevision,
  saveSkillDraft, transitionSkill,
  type DraftContent, type SkillAction, type SkillAdminItem, type SkillDetail,
} from '../../services/skillAdmin';

const labels = { draft: '草稿', in_review: '审核中', published: '已发布', deprecated: '已废弃', disabled: '已禁用', retired: '已撤销' };
const inputClass = 'w-full rounded-md border border-[var(--s-border-default)] bg-surface-card p-2 text-sm text-text-primary';
const buttonClass = 'rounded-md border border-[var(--s-border-default)] px-3 py-1.5 text-sm hover:bg-hover disabled:opacity-40';
const empty: DraftContent = { description: '', body: '', catalog_metadata: {} };

function errorMessage(error: unknown): string {
  if (error instanceof ApiRequestError) {
    if (error.status === 409) return '内容已被其他管理员修改，或标识已存在。请刷新后重试；未保存的编辑已保留。';
    if (error.status === 403) return '当前账号没有此组织 Skill 的管理权限。';
    if (error.status === 503) return 'Skill 服务或受控存储暂不可用，内容已保留，请稍后重试。';
    if (error.status === 422) return '操作未完成，请检查说明、正文、目录声明及当前审核状态。';
  }
  return '操作失败，内容已保留，请重试。';
}

export default function SkillAdminPanel({ orgId }: { orgId: string }) {
  const [items, setItems] = useState<SkillAdminItem[]>([]);
  const [detail, setDetail] = useState<SkillDetail | null>(null);
  const [content, setContent] = useState<DraftContent>(empty);
  const [metadata, setMetadata] = useState('{}');
  const [displayName, setDisplayName] = useState('');
  const [newKey, setNewKey] = useState('');
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [dirty, setDirty] = useState(false);
  const [confirmation, setConfirmation] = useState<'deprecate' | 'disable' | null>(null);
  const [history, setHistory] = useState<{ revision: string; content: DraftContent } | null>(null);
  const generation = useRef(0);

  useEffect(() => {
    const token = ++generation.current;
    setItems([]); setDetail(null); setHistory(null); setLoading(true); setError('');
    setDirty(false); setConfirmation(null); setContent(empty); setMetadata('{}');
    setDisplayName(''); setBusy(false); setNotice('');
    listManagedSkills(orgId).then(rows => {
      if (token === generation.current) setItems(rows);
    }).catch(e => {
      if (token === generation.current) setError(errorMessage(e));
    }).finally(() => {
      if (token === generation.current) setLoading(false);
    });
    return () => { generation.current = token + 1; };
  }, [orgId]);

  function show(value: SkillDetail) {
    setDetail(value); setContent(value.draft?.content ?? empty);
    const { name, ...advanced } = value.draft?.content.catalog_metadata ?? {};
    setDisplayName(typeof name === 'string' ? name : '');
    setMetadata(JSON.stringify(advanced, null, 2));
    setDirty(false); setHistory(null); setConfirmation(null);
  }

  async function perform(work: () => Promise<void>) {
    const token = generation.current;
    setBusy(true); setError(''); setNotice('');
    try { await work(); }
    catch (e) { if (token === generation.current) setError(errorMessage(e)); }
    finally { if (token === generation.current) setBusy(false); }
  }

  async function select(id: string) {
    const token = generation.current;
    await perform(async () => {
      const value = await getManagedSkill(orgId, id);
      if (token === generation.current) show(value);
    });
  }

  async function refreshList() {
    const token = generation.current;
    const rows = await listManagedSkills(orgId);
    if (token === generation.current) setItems(rows);
  }

  async function transition(action: SkillAction) {
    if (!detail) return;
    const token = generation.current;
    await perform(async () => {
      const value = await transitionSkill(orgId, detail.package_id, detail.draft?.version ?? 0, action);
      if (token !== generation.current) return;
      show(value);
      setNotice(action === 'publish' ? '发布成功，新 Turn 将使用此版本。' : '状态已更新。');
      await refreshList();
    });
  }

  const status = detail?.draft?.status;
  const editable = detail?.editable && status === 'draft';
  const terminal = status === 'deprecated' || status === 'disabled';

  return (
    <section className="space-y-4" aria-label="Skill 管理">
      <div>
        <h2 className="text-lg font-medium">组织 Skill</h2>
        <p className="text-sm text-text-tertiary">管理组织共享的操作说明。编辑草稿不会改变已发布版本，发布后新对话可用。</p>
      </div>
      {error && <p role="alert" className="text-error">{error}</p>}
      {notice && <p role="status" className="text-success">{notice}</p>}
      <form className="flex flex-wrap items-end gap-2" onSubmit={e => {
        e.preventDefault();
        const token = generation.current;
        void perform(async () => {
          const created = await createManagedSkill(orgId, newKey);
          const value = await getManagedSkill(orgId, created.package_id);
          if (token !== generation.current) return;
          show(value); setNewKey(''); await refreshList();
        });
      }}>
        <label className="text-sm">新 Skill 标识
          <input aria-label="新 Skill 标识" className={inputClass} value={newKey} maxLength={64}
            pattern="[a-z][a-z0-9_-]{0,63}" placeholder="例如 order-report" required
            disabled={busy || dirty} onChange={e => setNewKey(e.target.value)} />
        </label>
        <button className={buttonClass} disabled={busy || dirty || !newKey} type="submit">创建草稿</button>
        <button className={buttonClass} disabled={busy} type="button" onClick={() => void perform(refreshList)}>刷新列表</button>
      </form>
      {loading ? <p role="status">加载中...</p> : (
        <div className="grid gap-5 lg:grid-cols-[260px_minmax(0,1fr)]">
          <nav aria-label="Skill 列表" className="space-y-2">
            {items.length === 0 && <p className="text-sm text-text-tertiary">暂无 Skill，可创建组织草稿。</p>}
            {items.map(item => <button type="button" key={item.package_id}
              disabled={busy || dirty} aria-pressed={detail?.package_id === item.package_id}
              className={`w-full rounded-md border p-3 text-left ${detail?.package_id === item.package_id ? 'border-[var(--s-accent)]' : 'border-[var(--s-border-default)]'}`}
              onClick={() => void select(item.package_id)}>
              <span className="block font-medium break-all">{item.skill_key}</span>
              <span className="text-xs text-text-tertiary">{item.scope_kind === 'platform' ? '平台 · 只读' : '组织'} · {labels[item.status]}</span>
              {item.published_revision && <span className="block truncate text-xs text-text-tertiary" title={item.published_revision}>最近版本：{item.published_revision}</span>}
            </button>)}
          </nav>
          {detail ? <div className="min-w-0 space-y-4">
            <div className="flex flex-wrap items-center gap-2">
              <h3 className="font-medium">{detail.skill_key}</h3>
              {status && <span className="text-sm">{labels[status]}{detail.draft?.approved_by ? ' · 审核已通过' : ''}</span>}
              {!detail.editable && <span className="text-sm text-text-tertiary">平台 Skill，由平台维护</span>}
              <button className={buttonClass} disabled={busy} onClick={() => void select(detail.package_id)}>
                {dirty ? '放弃编辑并刷新' : '刷新详情'}
              </button>
            </div>
            {detail.draft && <>
              <label className="block text-sm">显示名称
                <input className={inputClass} value={displayName} maxLength={200} disabled={!editable || busy}
                  placeholder={detail.skill_key} onChange={e => { setDisplayName(e.target.value); setDirty(true); }} />
              </label>
              <label className="block text-sm">说明
                <input className={inputClass} value={content.description} maxLength={2000} disabled={!editable || busy}
                  onChange={e => { setContent({ ...content, description: e.target.value }); setDirty(true); }} />
              </label>
              <label className="block text-sm">Skill 正文（Markdown）
                <textarea className={`${inputClass} min-h-64 font-mono`} value={content.body} maxLength={1000000}
                  disabled={!editable || busy} onChange={e => { setContent({ ...content, body: e.target.value }); setDirty(true); }} />
              </label>
              <label className="block text-sm">目录声明（JSON）
                <textarea className={`${inputClass} min-h-32 font-mono`} value={metadata} disabled={!editable || busy}
                  onChange={e => { setMetadata(e.target.value); setDirty(true); }} />
              </label>
              <p className="text-xs text-text-tertiary">triggers 为触发提示；model_selectable 默认为 false；allowed_tool_names 仅收窄已有工具权限。</p>
            </>}
            {dirty && <p className="text-sm text-text-secondary">有未保存修改，请先保存或放弃编辑。</p>}
            {detail.editable && <div className="flex flex-wrap gap-2">
              {editable && <button className={buttonClass} disabled={busy || !dirty} onClick={() => {
                let parsed: unknown;
                try { parsed = JSON.parse(metadata); } catch { setError('目录声明必须是有效 JSON 对象。'); return; }
                if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) { setError('目录声明必须是 JSON 对象。'); return; }
                const token = generation.current;
                void perform(async () => {
                  const value = await saveSkillDraft(orgId, detail.package_id, detail.draft!.version,
                    { ...content, catalog_metadata: { ...parsed as Record<string, unknown>, name: displayName.trim() || null } });
                  if (token === generation.current) { show(value); setNotice('草稿已保存。'); await refreshList(); }
                });
              }}>保存草稿</button>}
              {editable && <button className={buttonClass} disabled={busy || dirty || !content.body.trim() || !content.description.trim()}
                onClick={() => void transition('submit')}>提交审核</button>}
              {status === 'in_review' && <>
                {!detail.draft?.approved_by && <button className={buttonClass} disabled={busy} onClick={() => void transition('approve')}>审核通过</button>}
                <button className={buttonClass} disabled={busy} onClick={() => void transition('reject')}>退回修改</button>
                {detail.draft?.approved_by && <button className={buttonClass} disabled={busy} onClick={() => void transition('publish')}>发布到本组织</button>}
              </>}
              {(!status || status === 'published') && <button className={buttonClass} disabled={busy} onClick={() => void transition('start_draft')}>创建修订草稿</button>}
              {!terminal && <button className={buttonClass} disabled={busy || dirty} onClick={() => setConfirmation('deprecate')}>废弃 Skill</button>}
              {status !== 'disabled' && <button className={buttonClass} disabled={busy || dirty} onClick={() => setConfirmation('disable')}>禁用 Skill</button>}
            </div>}
            {confirmation && <div role="alert" className="rounded-md border border-[var(--s-border-default)] p-3 space-y-2">
              <p>{confirmation === 'deprecate' ? '废弃后，新 Turn 无法使用；已激活的 Actor 仍可恢复原版本。此 Skill 将不能继续编辑或发布。' : '禁用后，新 Turn 和已激活的 Actor 恢复均被阻止。此 Skill 将不能继续编辑或发布。'}</p>
              <button className={buttonClass} disabled={busy} onClick={() => void transition(confirmation)}>确认{confirmation === 'deprecate' ? '废弃' : '禁用'}</button>
              <button className={buttonClass} disabled={busy} onClick={() => setConfirmation(null)}>取消</button>
            </div>}
            <div className="space-y-2">
              <h3 className="font-medium">版本历史（只读）</h3>
              {detail.revisions.length === 0 && <p className="text-sm text-text-tertiary">尚未发布版本。</p>}
              {detail.revisions.map(revision => <div key={revision.revision} className="flex flex-wrap items-center gap-2 text-sm">
                <code className="break-all">{revision.revision}</code><span>{labels[revision.status]}</span>
                <time>{new Date(revision.created_at).toLocaleString()}</time>
                <button className={buttonClass} disabled={busy} onClick={() => {
                  const token = generation.current;
                  void perform(async () => {
                    const body = await readSkillRevision(orgId, detail.package_id, revision.revision);
                    if (token === generation.current) setHistory({ revision: revision.revision, content: body });
                  });
                }}>查看 {revision.revision}</button>
              </div>)}
              {history && <div className="rounded-md border border-[var(--s-border-default)] p-3">
                <p className="break-all">{history.revision} · {history.content.description}</p>
                <pre className="mt-2 whitespace-pre-wrap break-words text-sm">{history.content.body}</pre>
                <button className={buttonClass} onClick={() => setHistory(null)}>收起版本正文</button>
              </div>}
            </div>
          </div> : <p className="text-sm text-text-tertiary">选择 Skill 查看草稿与版本历史。</p>}
        </div>
      )}
    </section>
  );
}
