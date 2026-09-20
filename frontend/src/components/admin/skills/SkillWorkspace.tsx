import { ArrowLeft, Check, ChevronRight, Ellipsis, Info, LockKeyhole, RefreshCw, SquarePen } from 'lucide-react';
import type { DraftContent, SkillAction, SkillDetail } from '../../../services/skillAdmin';
import { Button } from '../../ui/Button';
import { Dropdown, DropdownDivider, DropdownItem } from '../../ui/Dropdown';
import { SkillStatus } from './SkillStatus';
import { SkillDraftEditor } from './SkillDraftEditor';
import { SkillDocument } from './SkillDocument';
import { detailName, detailState, formatDate, revisionLabel } from './presentation';

export interface RevisionContent { revision: string; content: DraftContent }
interface Props {
  detail: SkillDetail; content: DraftContent; dirty: boolean; busy: boolean;
  tab: 'content' | 'history'; revisionContent: RevisionContent | null; reading: boolean; readFailed: boolean;
  onTab: (value: 'content' | 'history') => void; onChange: (value: DraftContent) => void;
  onBack: () => void; onRefresh: () => void; onSave: () => void; onSubmit: () => void;
  onAction: (value: SkillAction) => void; onRevision: (value: string) => void; onRetry: () => void;
}
export function SkillWorkspace(p: Props) {
  const d = p.detail, status = detailState(d), editing = d.editable && d.draft?.status === 'draft';
  const available = d.available_revision ? revisionLabel(d, d.available_revision) : null;
  const stopped = status === 'deprecated' || status === 'disabled';
  const notice = !d.editable ? '由平台维护，组织管理员可查看正文和发布记录。'
    : editing ? (available ? `正在编辑草稿，${available}继续可用。` : '草稿仅用于编辑，审核发布后才可使用。')
      : status === 'in_review' ? (d.draft?.approved_by ? '内容已审核通过，发布后才会启用新版本。' : '审核期间内容已锁定；需要修改时，请先退回草稿。')
        : status === 'deprecated' ? '已阻止新的解析和激活，已激活的任务仍可恢复。'
          : status === 'disabled' ? '已停止新的解析、激活和已有任务的恢复。'
            : '当前版本只读。编辑会创建新草稿，不改变正在使用的版本。';
  const usesDraft = !!d.draft && (editing || status === 'in_review' || (stopped && !d.revisions.length));
  const shown = p.tab === 'history' ? p.revisionContent?.content : usesDraft ? p.content : p.revisionContent?.content;
  return <>
    <div className="mb-4 flex items-center gap-2 text-xs text-[var(--s-text-tertiary)]">
      <Button variant="ghost" size="sm" icon={<ArrowLeft size={14} />} disabled={p.busy} onClick={p.onBack}>Skill 库</Button><ChevronRight size={13} /><span className="min-w-0 truncate">{detailName(d)}</span>
    </div>
    <div className="flex flex-wrap items-start justify-between gap-4">
      <div className="min-w-0"><div className="flex flex-wrap items-center gap-3"><h2 className="break-all text-xl font-semibold">{detailName(d)}</h2><SkillStatus state={status} approved={!!d.draft?.approved_by} /></div><p className="mt-1.5 break-all text-xs text-[var(--s-text-tertiary)]">{d.scope_kind === 'org' ? '组织 Skill' : '平台 Skill'} · {d.skill_key}</p></div>
      <div className="flex flex-wrap items-center gap-2">
        {editing && <><Button variant="secondary" disabled={p.busy || !p.dirty} onClick={p.onSave}>保存草稿</Button><Button disabled={p.busy} onClick={p.onSubmit}>{p.dirty ? '保存并提交审核' : '提交审核'}</Button></>}
        {d.editable && status === 'in_review' && <><Button variant="secondary" disabled={p.busy} onClick={() => p.onAction('reject')}>退回修改</Button><Button disabled={p.busy} onClick={() => p.onAction(d.draft?.approved_by ? 'publish' : 'approve')}>{d.draft?.approved_by ? '发布新版本' : '审核通过'}</Button></>}
        {d.editable && !editing && (status === 'published' || !d.draft && !stopped) && <Button icon={<SquarePen size={16} />} disabled={p.busy} onClick={() => p.onAction('start_draft')}>{d.revisions.length ? '编辑新版本' : '创建草稿'}</Button>}
        <Dropdown align="end" trigger={<Button variant="secondary" disabled={p.busy} aria-label="更多操作" icon={<Ellipsis size={16} />} />}>
          <div className="skill-admin-menu">
          <DropdownItem icon={<RefreshCw size={15} />} onClick={p.onRefresh}>刷新详情</DropdownItem>
          {d.editable && status !== 'disabled' && <><DropdownDivider />{status !== 'deprecated' && <DropdownItem disabled={p.dirty} variant="danger" onClick={() => p.onAction('deprecate')}>废弃 Skill</DropdownItem>}<DropdownItem disabled={p.dirty} variant="danger" onClick={() => p.onAction('disable')}>停用 Skill</DropdownItem></>}
          </div>
        </Dropdown>
      </div>
    </div>
    <p className="mt-4 flex items-start gap-2 text-xs leading-5 text-[var(--s-text-tertiary)]">{editing ? <Info size={15} className="mt-0.5 shrink-0" /> : <LockKeyhole size={15} className="mt-0.5 shrink-0" />}{notice}</p>
    <div className="mt-5 flex gap-6 border-b border-[var(--s-border-default)]" aria-label="Skill 详情视图">
      {(['content', 'history'] as const).map(tab => <button key={tab} type="button" aria-pressed={p.tab === tab} onClick={() => p.onTab(tab)} disabled={p.busy}
        className={`border-b-2 py-3 text-sm font-medium ${p.tab === tab ? 'border-[var(--s-accent)] text-[var(--s-accent)]' : 'border-transparent text-[var(--s-text-tertiary)]'}`}>
        {tab === 'content' ? editing ? '编辑草稿' : 'Skill 内容' : <>版本历史 <span className="ml-1 rounded bg-[var(--s-surface-sunken)] px-1.5 text-xs">{d.revisions.length}</span></>}
      </button>)}
    </div>
    <div className="mt-5 grid items-start gap-6 lg:grid-cols-[minmax(0,1fr)_180px]">
      <div className="min-w-0 overflow-hidden rounded-lg border border-[var(--s-border-default)] bg-[var(--s-surface-raised)]">
        {p.tab === 'content' && editing ? <SkillDraftEditor key={`${d.package_id}:${d.draft?.version}`} content={p.content} dirty={p.dirty} busy={p.busy} onChange={p.onChange} /> : <>
          {p.tab === 'history' && <div>
            {d.revisions.length ? d.revisions.map(row => <div key={row.revision} className="flex flex-wrap items-center justify-between gap-3 border-b border-[var(--s-border-default)] p-5">
              <div className="min-w-0"><div className="flex flex-wrap items-center gap-2"><h3 className="text-sm font-medium">{revisionLabel(d, row.revision)}</h3><SkillStatus state={row.status} />{row.revision === d.available_revision && <span className="flex items-center gap-1 text-xs text-[var(--s-success)]"><Check size={12} />当前可用</span>}</div><time className="mt-1 block text-xs text-[var(--s-text-tertiary)]" dateTime={row.created_at}>发布于 {formatDate(row.created_at)}</time><code className="mt-1 block break-all text-xs text-[var(--s-text-tertiary)]">{row.revision}</code></div>
              <Button variant="secondary" size="sm" disabled={p.busy || p.reading} onClick={() => p.onRevision(row.revision)} aria-label={`查看${revisionLabel(d, row.revision)}`}>查看内容</Button>
            </div>) : <p className="p-10 text-center text-sm text-[var(--s-text-tertiary)]">还没有发布记录，草稿发布后会保留在这里。</p>}
          </div>}
          {(p.tab === 'content' || p.revisionContent || p.reading || p.readFailed) && <>
            <div className="flex flex-wrap justify-between gap-2 border-b border-[var(--s-border-default)] px-5 py-3 text-xs text-[var(--s-text-tertiary)]"><span>{p.revisionContent ? revisionLabel(d, p.revisionContent.revision) : status === 'in_review' ? '待发布内容' : '正文预览'}</span><span>只读</span></div>
            <div className="p-5 sm:p-6">{p.reading ? <p role="status" className="text-sm text-[var(--s-text-tertiary)]">加载版本正文…</p> : p.readFailed ? <div className="space-y-3"><p className="text-sm text-[var(--s-text-tertiary)]">版本正文暂时无法读取。</p><Button variant="secondary" onClick={p.onRetry}>重试读取正文</Button></div> : shown ? <><p className="mb-5 text-sm text-[var(--s-text-tertiary)]">{shown.description}</p><SkillDocument body={shown.body} /></> : <p className="text-sm text-[var(--s-text-tertiary)]">尚未发布内容。</p>}</div>
          </>}
        </>}
      </div>
      <aside className="min-w-0 border-t border-[var(--s-border-default)] pt-4 text-xs lg:border-0 lg:pt-1">
        <h3 className="font-medium">Skill 信息</h3><dl className="mt-4 grid grid-cols-2 gap-5 lg:grid-cols-1">
          <div><dt className="text-[var(--s-text-tertiary)]">可用范围</dt><dd className="mt-1">{d.scope_kind === 'org' ? '当前组织' : '平台提供'}</dd></div>
          <div><dt className="text-[var(--s-text-tertiary)]">维护权限</dt><dd className="mt-1">{d.editable ? '组织管理员' : '平台维护 · 只读'}</dd></div>
          <div><dt className="text-[var(--s-text-tertiary)]">当前可用版本</dt><dd className="mt-1">{stopped ? '已停止新增使用' : available || (d.revisions.length ? '尚未启用' : '尚未发布')}</dd></div>
          <div><dt className="text-[var(--s-text-tertiary)]">唯一标识</dt><dd className="mt-1 break-all font-mono">{d.skill_key}</dd></div>
          {d.draft && <div><dt className="text-[var(--s-text-tertiary)]">最近更新</dt><dd className="mt-1">{formatDate(d.draft.updated_at)}</dd></div>}
        </dl>
        {!editing && shown && <details className="mt-5"><summary className="cursor-pointer text-[var(--s-text-tertiary)]">查看目录配置</summary><pre className="mt-2 whitespace-pre-wrap break-all rounded bg-[var(--s-surface-sunken)] p-2 text-xs">{JSON.stringify(shown.catalog_metadata, null, 2)}</pre></details>}
        {p.busy && <p role="status" className="mt-5 text-[var(--s-text-tertiary)]">正在处理，请稍候…</p>}
      </aside>
    </div>
  </>;
}
