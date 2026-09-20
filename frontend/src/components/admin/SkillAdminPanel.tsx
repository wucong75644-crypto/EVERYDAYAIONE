import { useEffect, useRef, useState } from 'react';
import { ApiRequestError } from '../../services/api';
import {
  createManagedSkill, getManagedSkill, listManagedSkills, readSkillRevision, saveSkillDraft, transitionSkill,
  type DraftContent, type SkillAction, type SkillAdminItem, type SkillDetail, type SkillState,
} from '../../services/skillAdmin';
import { Button } from '../ui/Button';
import { Input } from '../ui/Input';
import { Dialog, DialogFooter } from '../primitives/Dialog';
import { SkillLibrary, type SkillLibraryScope } from './skills/SkillLibrary';
import { SkillWorkspace, type RevisionContent } from './skills/SkillWorkspace';
import { detailState, emptyContent, type SkillNavigationState } from './skills/presentation';
import './skills/skill-admin.css';

function errorMessage(error: unknown): string {
  if (error instanceof ApiRequestError) {
    if (error.status === 409) return '内容已被其他管理员修改。未保存的编辑已保留，请复制需要保留的内容，再刷新查看最新版本。';
    if (error.status === 403) return '当前账号没有此组织 Skill 的管理权限。';
    if (error.status === 404) return 'Skill 或版本已不可用，请刷新列表。';
    if (error.status === 503) return 'Skill 服务或受控存储暂不可用，内容已保留，请稍后重试。';
    if (error.status === 422) return '请检查用途说明、正文、高级设置及当前审核状态。';
  }
  return '操作未完成，内容已保留，请重试；若结果不确定，请先刷新确认状态。';
}
const confirmations = {
  publish: ['发布新版本？', '审核通过的内容将保存为不可变版本。后续新的解析与激活将使用新版本，已经激活的任务继续使用原版本。', '确认发布'],
  reject: ['退回修改？', '内容将转回草稿，组织管理员可以继续编辑；再次发布前需要重新审核。', '退回草稿'],
  deprecate: ['废弃这项 Skill？', '废弃后会阻止新的解析和激活。已经激活的任务仍可使用原版本恢复。此 Skill 将不能继续编辑或发布，没有直接恢复入口。', '确认废弃'],
  disable: ['停用这项 Skill？', '停用后将阻止新的解析、激活和已有任务的恢复。仅在需要立即阻止使用时操作；没有直接恢复入口。', '确认停用'],
  leave: ['放弃未保存的修改？', '当前修改尚未保存。继续操作会放弃这些修改，保留上次保存的草稿。', '放弃修改并继续'],
} as const;
type Confirmation = keyof typeof confirmations;

export default function SkillAdminPanel({ orgId, onNavigationStateChange }: {
  orgId: string; onNavigationStateChange?: (state: SkillNavigationState) => void;
}) {
  const [items, setItems] = useState<SkillAdminItem[]>([]);
  const [detail, setDetail] = useState<SkillDetail | null>(null);
  const [content, setContent] = useState<DraftContent>(emptyContent);
  const [scope, setScope] = useState<SkillLibraryScope>('org');
  const [query, setQuery] = useState('');
  const [filter, setFilter] = useState<SkillState | ''>('');
  const [tab, setTab] = useState<'content' | 'history'>('content');
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [listFailed, setListFailed] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [dirty, setDirty] = useState(false);
  const [confirmation, setConfirmation] = useState<Confirmation | null>(null);
  const [creating, setCreating] = useState(false);
  const [newKey, setNewKey] = useState('');
  const [newName, setNewName] = useState('');
  const [revisionContent, setRevisionContent] = useState<RevisionContent | null>(null);
  const [reading, setReading] = useState(false);
  const [readFailed, setReadFailed] = useState(false);
  const [readTarget, setReadTarget] = useState<string | null>(null);
  const generation = useRef(0), readGeneration = useRef(0), busyRef = useRef(false);
  const detailOrg = useRef<string | null>(null);
  const pendingLeave = useRef<(() => void) | null>(null);

  useEffect(() => {
    const token = ++generation.current;
    detailOrg.current = null;
    busyRef.current = false;
    setItems([]); setDetail(null); setContent(emptyContent()); setLoading(true); setListFailed(false);
    setError(''); setNotice(''); setDirty(false); setBusy(false); setConfirmation(null); setCreating(false);
    setNewKey(''); setNewName(''); setScope('org'); setQuery(''); setFilter(''); setTab('content');
    setReadTarget(null); setRevisionContent(null); setReading(false); setReadFailed(false);
    pendingLeave.current = null;
    listManagedSkills(orgId).then(rows => {
      if (token === generation.current) setItems(rows);
    }).catch(e => {
      if (token === generation.current) { setError(errorMessage(e)); setListFailed(true); }
    }).finally(() => { if (token === generation.current) setLoading(false); });
    return () => { generation.current = token + 1; };
  }, [orgId]);

  useEffect(() => {
    onNavigationStateChange?.({ dirty, busy });
    const beforeUnload = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ''; };
    if (dirty || busy) window.addEventListener('beforeunload', beforeUnload);
    return () => window.removeEventListener('beforeunload', beforeUnload);
  }, [dirty, busy, onNavigationStateChange]);
  useEffect(() => () => onNavigationStateChange?.({ dirty: false, busy: false }), [onNavigationStateChange]);

  useEffect(() => {
    const token = generation.current, readToken = ++readGeneration.current;
    let cancelled = false;
    setRevisionContent(null); setReadFailed(false);
    if (!detail || !readTarget || detailOrg.current !== orgId) { setReading(false); return; }
    setReading(true);
    readSkillRevision(orgId, detail.package_id, readTarget).then(value => {
      if (!cancelled && token === generation.current && readToken === readGeneration.current) setRevisionContent({ revision: readTarget, content: value });
    }).catch(e => {
      if (!cancelled && token === generation.current && readToken === readGeneration.current) { setError(errorMessage(e)); setReadFailed(true); }
    }).finally(() => { if (!cancelled && token === generation.current && readToken === readGeneration.current) setReading(false); });
    return () => { cancelled = true; };
  }, [orgId, detail, readTarget]);

  function defaultRevision(value: SkillDetail) {
    const status = detailState(value);
    return value.draft && (status === 'draft' || status === 'in_review') ? null : value.revisions[0]?.revision ?? null;
  }
  function show(value: SkillDetail) {
    readGeneration.current++;
    detailOrg.current = orgId;
    setDetail(value); setContent(value.draft?.content ?? emptyContent()); setDirty(false);
    setTab('content'); setConfirmation(null); setRevisionContent(null); setReadFailed(false);
    const target = defaultRevision(value);
    setReadTarget(target); setReading(!!target);
  }
  async function perform(work: (token: number) => Promise<void>) {
    if (busyRef.current) return;
    const token = generation.current;
    busyRef.current = true; setBusy(true); setError(''); setNotice('');
    try { await work(token); }
    catch (e) { if (token === generation.current) setError(errorMessage(e)); }
    finally { if (token === generation.current) { busyRef.current = false; setBusy(false); } }
  }
  async function refreshList(token: number, afterWrite = false) {
    try {
      const rows = await listManagedSkills(orgId);
      if (token === generation.current) { setItems(rows); setListFailed(false); }
    } catch (e) {
      if (token === generation.current) { setListFailed(true); setError(`${afterWrite ? '操作已完成，但列表刷新失败。' : ''}${errorMessage(e)}`); }
    }
  }
  function select(id: string) {
    void perform(async token => {
      const value = await getManagedSkill(orgId, id);
      if (token === generation.current) show(value);
    });
  }
  function requestLeave(action: () => void) {
    if (busyRef.current) return;
    if (dirty) { pendingLeave.current = action; setConfirmation('leave'); }
    else action();
  }
  function back() {
    requestLeave(() => { readGeneration.current++; setDetail(null); setReadTarget(null); setRevisionContent(null); setDirty(false); setError(''); setNotice(''); });
  }
  function changeTab(value: 'content' | 'history') {
    if (!detail || value === tab) return;
    readGeneration.current++;
    const target = value === 'content' ? defaultRevision(detail) : null;
    setTab(value); setReadTarget(target); setRevisionContent(null); setReading(!!target); setReadFailed(false);
  }
  function action(value: SkillAction) {
    if (value in confirmations) { setError(''); setConfirmation(value as Confirmation); return; }
    void transition(value);
  }
  async function transition(value: SkillAction) {
    if (!detail) return;
    await perform(async token => {
      const next = await transitionSkill(orgId, detail.package_id, detail.draft?.version ?? 0, value);
      if (token !== generation.current) return;
      show(next);
      setNotice(value === 'publish' ? '发布成功，新版本已可用。' : value === 'start_draft' ? '修订草稿已创建，原版本保持不变。' : '状态已更新。');
      await refreshList(token, true);
    });
  }
  function save(submit = false) {
    if (!detail?.draft) return;
    if (submit && (!content.body.trim() || !content.description.trim())) { setError('请先填写用途说明和操作说明，再提交审核。'); return; }
    void perform(async token => {
      let current = detail;
      if (dirty) {
        const name = content.catalog_metadata.name;
        current = await saveSkillDraft(orgId, detail.package_id, detail.draft!.version, { ...content,
          catalog_metadata: { ...content.catalog_metadata, ...(typeof name === 'string' ? { name: name.trim() || null } : {}) } });
        if (token !== generation.current) return;
        show(current); setNotice('草稿已保存。');
      }
      if (submit) {
        try {
          const next = await transitionSkill(orgId, current.package_id, current.draft!.version, 'submit');
          if (token !== generation.current) return;
          show(next); setNotice('已提交审核，内容已锁定。');
        } catch (e) {
          if (token !== generation.current) return;
          setError(`${dirty ? '草稿已保存，但提交审核失败。' : '提交审核失败。'}${errorMessage(e)}`);
        }
      }
      if (token === generation.current) await refreshList(token, true);
    });
  }
  function create() {
    if (!newName.trim()) { setError('请填写 Skill 名称。'); return; }
    void perform(async token => {
      let created: { package_id: string };
      try {
        created = await createManagedSkill(orgId, newKey, { description: '', body: `# ${newName.trim()}\n\n## 使用场景\n\n## 执行步骤\n\n## 输出要求\n`, catalog_metadata: { name: newName.trim() } });
      } catch (e) {
        if (token === generation.current) setError(e instanceof ApiRequestError && e.status === 409 ? '该标识已存在，请换一个标识。' : errorMessage(e));
        return;
      }
      if (token !== generation.current) return;
      setCreating(false); setNewKey(''); setNewName(''); setScope('org'); setQuery(''); setFilter('');
      setNotice('草稿已创建。');
      await refreshList(token, true);
      if (token !== generation.current) return;
      try {
        const next = await getManagedSkill(orgId, created.package_id);
        if (token === generation.current) show(next);
      } catch (e) { if (token === generation.current) setError(`草稿已创建，但详情加载失败，请从列表重新打开。${errorMessage(e)}`); }
    });
  }
  const modalOpen = !!confirmation || creating;
  return <section aria-label="Skill 管理" className="skill-admin-theme py-3 text-[var(--s-text-primary)]">
    {error && !modalOpen && scope !== 'personal' && <p role="alert" className="mb-4 rounded-md bg-[var(--s-error-soft)] px-4 py-3 text-sm text-[var(--s-error)]">{error}</p>}
    {notice && scope !== 'personal' && <p role="status" className="mb-4 rounded-md bg-[var(--s-success-soft)] px-4 py-3 text-sm text-[var(--s-success)]">{notice}</p>}
    {detail ? <SkillWorkspace detail={detail} content={content} dirty={dirty} busy={busy} tab={tab} revisionContent={revisionContent} reading={reading} readFailed={readFailed}
      onTab={changeTab} onChange={value => { setContent(value); setDirty(true); }} onBack={back} onRefresh={() => requestLeave(() => select(detail.package_id))}
      onSave={() => save()} onSubmit={() => save(true)} onAction={action} onRevision={revision => {
        if (revision === readTarget && !readFailed) return;
        setError(''); setRevisionContent(null); setReading(true); setReadTarget(revision);
        if (revision === readTarget) setDetail({ ...detail });
      }}
      onRetry={() => { setError(''); setDetail({ ...detail }); }} />
      : <SkillLibrary items={items} loading={loading} failed={listFailed} busy={busy} scope={scope} query={query} filter={filter}
        onScope={value => { setScope(value); setQuery(''); setFilter(''); }} onQuery={setQuery} onFilter={setFilter}
        onCreate={() => { setError(''); setCreating(true); }} onOpen={select} onRefresh={() => { void perform(token => refreshList(token)); }} />}
    <Dialog className="skill-admin-theme" open={creating} onOpenChange={value => { if (!busy) { setCreating(value); setError(''); } }} title="新建组织 Skill"
      description="从草稿开始，审核发布后供组织使用。" closeOnEscape={!busy} closeOnOutsideClick={!busy} showClose={!busy}>
      <form className="mt-5 space-y-4" onSubmit={e => { e.preventDefault(); create(); }}>
        <Input label="名称" value={newName} maxLength={200} required disabled={busy} placeholder="例如：订单日报" onChange={e => setNewName(e.target.value)} />
        <Input label="唯一标识" value={newKey} maxLength={64} pattern="[a-z][a-z0-9_-]{0,63}" required disabled={busy} placeholder="例如：order-report" onChange={e => setNewKey(e.target.value)} />
        <p className="text-xs text-[var(--s-text-tertiary)]">以小写字母开头，可包含数字、短横线或下划线。创建后不可修改。</p>
        {error && <p role="alert" className="text-sm text-[var(--s-error)]">{error}</p>}
        <DialogFooter><Button type="button" variant="secondary" disabled={busy} onClick={() => { setCreating(false); setError(''); }}>取消</Button><Button type="submit" loading={busy}>创建草稿</Button></DialogFooter>
      </form>
    </Dialog>
    <Dialog className="skill-admin-theme" open={!!confirmation} onOpenChange={value => { if (!value && !busy) { setConfirmation(null); setError(''); pendingLeave.current = null; } }}
      title={confirmation ? confirmations[confirmation][0] : ''} description={confirmation ? confirmations[confirmation][1] : ''}
      closeOnEscape={!busy} closeOnOutsideClick={!busy} showClose={!busy}>
      {error && <p role="alert" className="mt-3 text-sm text-[var(--s-error)]">{error}</p>}
      <DialogFooter><Button variant="secondary" disabled={busy} onClick={() => { setConfirmation(null); setError(''); pendingLeave.current = null; }}>{confirmation === 'leave' ? '继续编辑' : '取消'}</Button>
        <Button variant={confirmation === 'deprecate' || confirmation === 'disable' || confirmation === 'leave' ? 'danger' : 'accent'} loading={busy} onClick={() => {
          if (!confirmation) return;
          if (confirmation === 'leave') { const pending = pendingLeave.current; setConfirmation(null); pendingLeave.current = null; pending?.(); }
          else void transition(confirmation);
        }}>{confirmation ? confirmations[confirmation][2] : '确认'}</Button></DialogFooter>
    </Dialog>
  </section>;
}
