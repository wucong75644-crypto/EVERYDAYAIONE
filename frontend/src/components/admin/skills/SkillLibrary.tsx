import { Building2, ChevronRight, FileText, Layers, LockKeyhole, Plus, RefreshCw, Search, UserRound } from 'lucide-react';
import type { SkillAdminItem, SkillState } from '../../../services/skillAdmin';
import { Button } from '../../ui/Button';
import { Input, inputVariants } from '../../ui/Input';
import { SkillStatus } from './SkillStatus';
import { formatDate, stateLabels } from './presentation';

export type SkillLibraryScope = 'personal' | 'org' | 'platform';
const sources = [
  { scope: 'personal', label: '我的 Skill', icon: UserRound },
  { scope: 'org', label: '组织 Skill', icon: Building2 },
  { scope: 'platform', label: '平台 Skill', icon: Layers },
] as const;

interface Props {
  items: SkillAdminItem[]; loading: boolean; busy: boolean; failed: boolean;
  scope: SkillLibraryScope; query: string; filter: SkillState | '';
  onScope: (value: SkillLibraryScope) => void; onQuery: (value: string) => void;
  onFilter: (value: SkillState | '') => void; onCreate: () => void;
  onOpen: (id: string) => void; onRefresh: () => void;
}
export function SkillLibrary(p: Props) {
  const scoped = p.items.filter(item => item.scope_kind === p.scope);
  const rows = scoped.filter(item => (!p.filter || item.status === p.filter)
    && `${item.name ?? ''} ${item.skill_key} ${item.working_description ?? item.description ?? ''}`.toLowerCase().includes(p.query.trim().toLowerCase()));
  return <>
    <div className="flex flex-wrap items-center justify-between gap-4">
      <div><h2 className="text-2xl font-semibold tracking-tight">Skill 库</h2><p className="mt-1 text-sm text-[var(--s-text-tertiary)]">把个人和团队的工作方法，变成可以复用的能力。</p></div>
      <Button icon={<Plus size={16} />} disabled={p.busy || p.scope === 'personal'} onClick={p.scope === 'personal' ? undefined : p.onCreate}
        aria-describedby={p.scope === 'personal' ? 'personal-skill-availability' : undefined}>{p.scope === 'personal' ? '新建个人 Skill' : '新建 Skill'}</Button>
    </div>
    <div className="mt-6 flex gap-4 overflow-x-auto border-b border-[var(--s-border-default)] sm:gap-6" aria-label="Skill 来源">
      {sources.map(({ scope, label, icon: Icon }) => <button type="button" key={scope} aria-pressed={p.scope === scope} disabled={p.busy}
        onClick={() => p.onScope(scope)} className={`flex shrink-0 items-center gap-2 whitespace-nowrap border-b-2 py-3 text-sm font-medium disabled:opacity-50 ${p.scope === scope ? 'border-[var(--s-accent)] text-[var(--s-accent)]' : 'border-transparent text-[var(--s-text-secondary)]'}`}>
        <Icon size={16} />{label}
        {scope !== 'personal' && <span className="rounded bg-[var(--s-surface-sunken)] px-1.5 text-xs text-[var(--s-text-tertiary)]">{p.items.filter(i => i.scope_kind === scope).length}</span>}
      </button>)}
    </div>
    {p.scope === 'personal' ? <div className="mt-5 rounded-lg border border-[var(--s-border-default)] bg-[var(--s-surface-raised)] px-6 py-14 text-center">
      <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-xl bg-[var(--s-selected)] text-[var(--s-accent)]"><UserRound size={24} /></div>
      <h3 className="mt-5 text-base font-semibold">你的专属 Skill 空间</h3>
      <p className="mx-auto mt-2 max-w-sm text-sm leading-6 text-[var(--s-text-secondary)]">整理你常用的工作方法和操作说明，让重复的事情更简单。</p>
      <span className="mt-4 inline-flex items-center gap-1.5 rounded-full bg-[var(--s-surface-sunken)] px-3 py-1 text-xs text-[var(--s-text-tertiary)]"><LockKeyhole size={12} />规划为私有草稿 · 仅自己可见</span>
      <p id="personal-skill-availability" className="mt-6 text-xs leading-5 text-[var(--s-text-tertiary)]">个人 Skill 的创建与编辑尚未开放。</p>
    </div> : <>
    <div className="my-4 flex flex-wrap items-center justify-between gap-3">
      <div className="w-full sm:max-w-xs"><Input aria-label="搜索 Skill" placeholder="搜索名称或标识…" icon={<Search size={16} />} value={p.query} onChange={e => p.onQuery(e.target.value)} /></div>
      <div className="flex items-center gap-2">
        <select aria-label="筛选状态" className={inputVariants({ fullWidth: false })} value={p.filter} onChange={e => p.onFilter(e.target.value as SkillState | '')}>
          <option value="">全部状态</option>{Object.entries(stateLabels).filter(([key]) => key !== 'retired').map(([key, label]) => <option key={key} value={key}>{label}</option>)}
        </select>
        <Button variant="ghost" aria-label="刷新列表" disabled={p.busy || p.loading} onClick={p.onRefresh} icon={<RefreshCw size={16} />} />
      </div>
    </div>
    <div className="overflow-hidden rounded-lg border border-[var(--s-border-default)] bg-[var(--s-surface-raised)]">
      <div className="grid grid-cols-[minmax(0,1fr)_88px] gap-3 bg-[var(--s-surface-sunken)] px-4 py-3 text-xs text-[var(--s-text-tertiary)] md:grid-cols-[minmax(0,1fr)_96px_110px_104px_16px]">
        <span>名称</span><span>工作状态</span><span className="hidden md:block">可用版本</span><span className="hidden md:block">最近更新</span>
      </div>
      {p.loading ? <p role="status" className="p-12 text-center text-sm text-[var(--s-text-tertiary)]">加载 Skill…</p> : rows.length ? rows.map(item => {
        const available = ['deprecated', 'disabled'].includes(item.status) ? '已停止使用'
          : item.available_revision ? (item.available_revision_number ? `第 ${item.available_revision_number} 版` : item.available_revision) : item.published_revision ? '尚未启用' : '尚未发布';
        return <button type="button" key={item.package_id} aria-label={`打开 ${item.name || item.skill_key}`} disabled={p.busy}
          onClick={() => p.onOpen(item.package_id)} className="grid w-full grid-cols-[minmax(0,1fr)_88px] items-center gap-3 border-t border-[var(--s-border-default)] px-4 py-5 text-left transition-colors hover:bg-[var(--s-selected)] disabled:opacity-50 md:grid-cols-[minmax(0,1fr)_96px_110px_104px_16px]">
          <span className="flex min-w-0 items-start gap-3"><span className="hidden rounded-md border border-[var(--s-border-default)] bg-[var(--s-surface-sunken)] p-2 text-[var(--s-text-tertiary)] sm:block"><FileText size={17} /></span><span className="min-w-0"><span className="block break-words text-sm font-medium">{item.name || item.skill_key}</span><span className="mt-1 block break-words text-xs text-[var(--s-text-tertiary)]">{item.working_description || item.description || '尚未填写用途说明'}</span><span className="mt-1 block text-xs text-[var(--s-text-tertiary)] md:hidden">{available}</span></span></span>
          <span><SkillStatus state={item.status} approved={item.approved} /></span>
          <span className="hidden break-all text-xs text-[var(--s-text-secondary)] md:block">{available}{item.status === 'draft' && item.available_revision && <span className="mt-1 block text-[var(--s-text-tertiary)]">仍在使用</span>}</span>
          <time className="hidden text-xs tabular-nums text-[var(--s-text-tertiary)] md:block" dateTime={item.updated_at || undefined}>{formatDate(item.updated_at)}</time><ChevronRight size={15} className="hidden text-[var(--s-text-tertiary)] md:block" />
        </button>;
      }) : <div className="px-4 py-12 text-center text-sm text-[var(--s-text-secondary)]">
        <p>{p.failed ? 'Skill 列表暂时无法加载' : p.query || p.filter ? '没有符合条件的 Skill' : p.scope === 'org' ? '还没有组织 Skill' : '暂无平台 Skill'}</p>
        <p className="mt-2 text-xs text-[var(--s-text-tertiary)]">{p.failed ? '请刷新列表重试。' : p.query || p.filter ? '试试其他关键词或状态。' : p.scope === 'org' ? '创建草稿，整理团队可以复用的操作说明。' : '平台发布的内容会展示在这里。'}</p>
        {!p.failed && !p.query && !p.filter && p.scope === 'org' && <Button variant="secondary" className="mt-4" onClick={p.onCreate}>创建第一个 Skill</Button>}
      </div>}
    </div>
    <p className="mt-3 text-xs text-[var(--s-text-tertiary)]">{p.scope === 'org' ? '组织管理员共同维护 · 发布后供组织使用' : '平台统一维护 · 可查看内容和版本历史'}</p>
    </>}
  </>;
}
