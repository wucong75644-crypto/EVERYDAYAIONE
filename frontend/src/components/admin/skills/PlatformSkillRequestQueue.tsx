import { useCallback, useEffect, useState } from 'react';
import { Check, ChevronDown, Loader2, RotateCw, X } from 'lucide-react';
import { skillCreationService, type SkillChatProposal } from '../../../services/skillCreation';
import { Button } from '../../ui/Button';

export function PlatformSkillRequestQueue({ onResolved }: { onResolved: () => void }) {
  const [rows, setRows] = useState<SkillChatProposal[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [busyId, setBusyId] = useState('');
  const [expanded, setExpanded] = useState<string | null>(null);
  const refresh = useCallback(async () => {
    setLoading(true);
    try { setRows(await skillCreationService.listPlatformChatProposals()); setError(''); }
    catch { setError('平台 Skill 申请暂时无法读取。'); }
    finally { setLoading(false); }
  }, []);
  useEffect(() => { void refresh(); }, [refresh]);

  const decide = async (row: SkillChatProposal, action: 'approve' | 'reject') => {
    const verb = action === 'approve' ? '审核并发布' : '退回申请';
    if (!window.confirm(`${verb}「${String(row.content.catalog_metadata?.name || row.skill_key)}」？`)) return;
    setBusyId(row.id); setError('');
    try {
      await skillCreationService.decidePlatformChatProposal(row.id, action);
      await refresh();
      onResolved();
    } catch (reason) { setError(reason instanceof Error ? reason.message : `${verb}失败，请重试。`); }
    finally { setBusyId(''); }
  };

  return <section className="mb-6 rounded-xl border border-[var(--s-border-default)] bg-[var(--s-surface-raised)] p-4" aria-label="待审核的平台 Skill 申请">
    <div className="flex items-center justify-between gap-3"><div><h3 className="text-sm font-semibold">待审核的平台 Skill 申请</h3><p className="mt-1 text-xs text-[var(--s-text-tertiary)]">申请人确认后提交；只有平台管理员可以审核并发布。</p></div><Button variant="ghost" size="sm" aria-label="刷新平台申请" disabled={loading || !!busyId} onClick={() => void refresh()} icon={<RotateCw size={15} />} /></div>
    {error && <p role="alert" className="mt-3 text-xs text-[var(--s-error)]">{error}</p>}
    {loading ? <p role="status" className="mt-4 flex items-center gap-2 text-xs text-[var(--s-text-tertiary)]"><Loader2 size={14} className="animate-spin" />读取申请…</p>
      : rows.length === 0 ? <p className="mt-4 text-xs text-[var(--s-text-tertiary)]">目前没有待处理申请。</p>
        : <div className="mt-3 divide-y divide-[var(--s-border-default)]">{rows.map(row => {
          const name = String(row.content.catalog_metadata?.name || row.skill_key);
          return <article key={row.id} className="py-3 first:pt-1 last:pb-1">
            <div className="flex flex-wrap items-center justify-between gap-3"><div className="min-w-0"><h4 className="text-sm font-medium">{name}</h4><p className="mt-1 text-xs text-[var(--s-text-secondary)]">{row.content.description || '未填写用途说明'} · {row.source_message_refs?.length ?? 0} 条来源摘要</p></div>
              <div className="flex items-center gap-2"><Button variant="secondary" size="sm" disabled={!!busyId} onClick={() => setExpanded(expanded === row.id ? null : row.id)}><ChevronDown size={14} className="mr-1" />{expanded === row.id ? '收起' : '查看正文'}</Button><Button variant="secondary" size="sm" disabled={!!busyId} loading={busyId === row.id} onClick={() => void decide(row, 'reject')}><X size={14} className="mr-1" />退回</Button><Button size="sm" disabled={!!busyId} loading={busyId === row.id} onClick={() => void decide(row, 'approve')}><Check size={14} className="mr-1" />审核并发布</Button></div>
            </div>
            {expanded === row.id && <pre className="mt-3 max-h-64 overflow-auto whitespace-pre-wrap break-words rounded-lg bg-[var(--s-surface-sunken)] p-3 text-xs leading-5">{row.content.body}</pre>}
          </article>;
        })}</div>}
  </section>;
}
