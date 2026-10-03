import { useCallback, useEffect, useState } from 'react';
import { BookOpen, Check, Loader2, ThumbsDown, ThumbsUp, X } from 'lucide-react';
import { skillCreationService, type SkillChatProposal } from '../../../services/skillCreation';
import { Button } from '../../ui/Button';

type Target = 'personal' | 'org' | 'platform';
type RefreshOptions = { preserveError?: boolean };
const targetLabels: Record<Target, string> = {
  personal: '个人 · 仅自己使用', org: '组织 · 提交组织审核', platform: '平台 · 申请平台审核',
};

function actionErrorMessage(reason: unknown): string {
  if (reason && typeof reason === 'object') {
    const value = reason as {
      code?: unknown;
      response?: { data?: { detail?: unknown; error?: { code?: unknown } } };
    };
    const code = typeof value.code === 'string' ? value.code
      : typeof value.response?.data?.error?.code === 'string' ? value.response.data.error.code
        : typeof value.response?.data?.detail === 'string' ? value.response.data.detail : '';
    if (code === 'SKILL_STORAGE_WRITE_REJECTED') {
      return 'Skill 存储当前不可写，候选已保留。请联系管理员处理后重试。';
    }
  }
  return reason instanceof Error && !/^Request failed with status code \d+$/.test(reason.message)
    ? reason.message : '操作未完成，候选仍保留，请刷新状态后重试。';
}

export default function SkillChatProposalCard({ proposalId, fallbackTitle }: {
  proposalId: string; fallbackTitle?: string;
}) {
  const [proposal, setProposal] = useState<SkillChatProposal | null>(null);
  const [target, setTarget] = useState<Target>('personal');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const refresh = useCallback(async ({ preserveError = false }: RefreshOptions = {}) => {
    try {
      const next = await skillCreationService.getChatProposal(proposalId);
      setProposal(next);
      if (!preserveError) setError('');
      setTarget(current => next.available_targets?.[current] === false
        ? (['personal', 'org', 'platform'] as const).find(scope => next.available_targets?.[scope] === true) || current
        : current);
    }
    catch { if (!preserveError) setError('暂时无法读取 Skill 候选，请刷新页面后重试。'); }
  }, [proposalId]);
  useEffect(() => { void refresh(); }, [refresh]);

  const act = async (operation: 'confirm' | 'cancel') => {
    if (!proposal || busy) return;
    setBusy(true); setError('');
    try {
      if (operation === 'confirm') {
        const result = await skillCreationService.confirmChatProposal(proposalId, {
          expected_version: proposal.version, content_sha256: proposal.content_sha256, target_scope: target,
        });
        setProposal({ ...proposal, ...result, status: result.status, target_scope: target });
      } else {
        await skillCreationService.cancelChatProposal(proposalId);
        setProposal({ ...proposal, status: 'cancelled' });
      }
    } catch (reason) {
      setError(actionErrorMessage(reason));
      await refresh({ preserveError: true });
    } finally { setBusy(false); }
  };
  const sendFeedback = async (rating: 'helpful' | 'not_helpful') => {
    try {
      await skillCreationService.feedbackChatProposal(proposalId, rating);
      setProposal(current => current ? { ...current, feedback_rating: rating } : current);
    } catch { setError('反馈暂未保存，请稍后重试。'); }
  };

  if (!proposal) return <div className="my-3 flex items-center gap-2 rounded-xl border border-border-default bg-surface px-4 py-3 text-sm text-text-secondary">
    <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />{error || '正在读取 Skill 候选…'}
  </div>;

  const metadata = proposal.content.catalog_metadata || {};
  const name = String(metadata.name || fallbackTitle || 'Skill 候选');
  const updating = proposal.operation === 'update';
  const awaiting = proposal.status === 'awaiting_confirmation';
  const complete = proposal.status === 'committed';
  const targetAvailable = proposal.available_targets?.[target] !== false;
  const message = proposal.status === 'awaiting_review' ? '已提交平台审核，平台管理员审核后发布。'
    : complete ? (proposal.result?.message || (updating ? '个人 Skill 已更新，原有版本保留。' : 'Skill 已创建。'))
      : proposal.status === 'cancelled' ? '已取消此候选。'
        : proposal.status === 'rejected' ? '平台审核未通过。'
        : proposal.status === 'expired' ? '候选已过期，请重新整理。'
          : updating ? '修改现有个人 Skill，确认后发布为新版本；当前版本继续保留。'
            : '核对完整内容，选择 Skill 的保存范围。确认前不会保存或发布。';

  return <section className="my-3 min-w-0 overflow-hidden rounded-2xl border border-border-default bg-surface shadow-sm" aria-label={updating ? 'Skill 修改预览' : 'Skill 创建预览'}>
    <header className="flex items-start gap-3 border-b border-border-default bg-surface-elevated px-4 py-3">
      <span className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-accent/10 text-accent"><BookOpen className="h-4 w-4" aria-hidden="true" /></span>
      <div className="min-w-0 flex-1"><h3 className="break-words text-sm font-semibold text-text-primary">{name}</h3><p className="mt-1 text-xs text-text-secondary">{message}</p></div>
      <span className="shrink-0 rounded-full bg-active px-2 py-1 text-[11px] font-medium text-text-secondary">{awaiting ? '待你确认' : complete ? (updating ? '已更新' : '已创建') : proposal.status === 'awaiting_review' ? '待平台审核' : proposal.status}</span>
    </header>
    <div className="space-y-3 px-4 py-4">
      <div><p className="text-xs font-medium text-text-secondary">用途</p><p className="mt-1 whitespace-pre-wrap text-sm text-text-primary">{proposal.content.description || '未填写用途说明'}</p></div>
      {updating && <div className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-border-default bg-surface-elevated px-3 py-2 text-xs">
        <span className="font-medium text-text-primary">修改目标：个人 Skill · 仅你本人</span>
        {proposal.target_revision && <span className="text-text-secondary">基于当前版本 {proposal.target_revision}</span>}
      </div>}
      <details className="rounded-lg border border-border-default bg-surface px-3 py-2">
        <summary className="cursor-pointer text-xs font-medium text-text-secondary">查看完整 Skill 正文</summary>
        <pre className="mt-3 max-h-72 overflow-auto whitespace-pre-wrap break-words text-xs leading-5 text-text-primary">{proposal.content.body}</pre>
      </details>
      {awaiting && !updating && <fieldset>
        <legend className="text-xs font-medium text-text-secondary">保存范围</legend>
        <div className="mt-2 grid gap-2 sm:grid-cols-3">
          {(['personal', 'org', 'platform'] as const).map(value => {
            const available = proposal.available_targets?.[value] !== false;
            return <label key={value} className={`flex cursor-pointer items-start gap-2 rounded-lg border px-3 py-2 text-xs ${target === value ? 'border-accent bg-accent/5 text-text-primary' : 'border-border-default text-text-secondary'} ${!available ? 'cursor-not-allowed opacity-45' : ''}`}>
              <input type="radio" name={`scope-${proposalId}`} value={value} checked={target === value} disabled={!available || busy} onChange={() => setTarget(value)} />
              <span>{targetLabels[value]}</span>
            </label>;
          })}
        </div>
        {target === 'personal' && <p className="mt-2 text-xs text-text-tertiary">确认后立即发布为个人 Skill，仅你本人可以查看和使用。</p>}
        {target === 'org' && <p className="mt-2 text-xs text-text-tertiary">确认后创建组织待审核草稿，不会自动通过审核。</p>}
        {target === 'platform' && <p className="mt-2 text-xs text-text-tertiary">确认后提交平台管理员审核；管理员批准后才会发布。</p>}
        {!targetAvailable && <p role="alert" className="mt-2 text-xs text-amber-700">当前组织已关闭此发布范围，请选择其他范围或联系组织管理员。</p>}
        {Object.values(proposal.available_targets || {}).every(value => value === false) && <p role="alert" className="mt-2 text-xs text-amber-700">当前组织已关闭 AI Skill 创建权限，此候选无法确认。</p>}
      </fieldset>}
      {error && <p role="alert" className="text-xs text-red-600">{error}</p>}
      {awaiting ? <div className="flex flex-wrap justify-end gap-2">
        <Button variant="secondary" size="sm" disabled={busy} onClick={() => void act('cancel')}><X className="mr-1 h-3.5 w-3.5" />取消</Button>
        <Button size="sm" disabled={busy || !targetAvailable} loading={busy} onClick={() => void act('confirm')}><Check className="mr-1 h-3.5 w-3.5" />{updating ? '确认发布更新' : '确认并继续'}</Button>
      </div> : <div className="flex items-center justify-between border-t border-border-default pt-3">
        <span className="text-xs text-text-tertiary">这次整理结果对你有帮助吗？</span>
        <div className="flex gap-1"><Button variant="ghost" size="sm" aria-label="有帮助" disabled={!!proposal.feedback_rating} onClick={() => void sendFeedback('helpful')}><ThumbsUp className="h-4 w-4" /></Button><Button variant="ghost" size="sm" aria-label="没有帮助" disabled={!!proposal.feedback_rating} onClick={() => void sendFeedback('not_helpful')}><ThumbsDown className="h-4 w-4" /></Button></div>
      </div>}
    </div>
  </section>;
}
