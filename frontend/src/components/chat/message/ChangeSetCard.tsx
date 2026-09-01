import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react';
import {
  AlertTriangle,
  Check,
  CheckCircle2,
  ChevronDown,
  Clock3,
  GitCompare,
  Loader2,
  RefreshCw,
  ShieldCheck,
  X,
  XCircle,
} from 'lucide-react';
import type {
  ChangeCheck,
  ChangeEvent,
  ChangeSet,
  ChangeSetStatus,
} from '../../../types/changeset';
import { changeSetService } from '../../../services/changeSet';
import { ApiRequestError } from '../../../services/api';
import {
  CHANGESET_UPDATED_EVENT,
  dispatchChangeSetAction,
  type ChangeSetAction,
} from '../../../services/changeSetEvents';
import { Button } from '../../ui/Button';
import { cn } from '../../../utils/cn';
import { MESSAGE_CONTENT_LAYOUT } from './messageContentLayout';
import {
  displayValue,
  getChangeSetResourceAdapter,
  isDisplayableKey,
  type ChangeSetDiffEntry,
  type ChangeSetDisplayField,
  type ChangeSetResourceAdapter,
} from './changeSetResourceAdapters';

interface ChangeSetCardProps {
  changeSetId: string;
  fallbackTitle?: string;
  resourceType?: string;
  adapter?: ChangeSetResourceAdapter;
  actionHandlers?: Partial<Record<ChangeSetAction, ChangeSetActionHandler>>;
  /** 宿主可据已确认的最终状态刷新自身的业务投影。 */
  onChangeSetUpdated?: (changeSet: ChangeSet) => void;
}

export type ChangeSetActionHandler = (
  changeSet: ChangeSet,
) => Promise<ChangeSet | void> | ChangeSet | void;

const statusLabels: Record<ChangeSetStatus, string> = {
  draft: '草案', resolving: '规划中', proposed: '待校验', validating: '校验中',
  preflighting: '试跑中', awaiting_approval: '待确认', committing: '提交中',
  applied: '已提交', cancelled: '已取消', rejected: '审批拒绝', failed: '失败',
  expired: '已过期', conflicted: '发生冲突',
};

const statusDescriptions: Record<ChangeSetStatus, string> = {
  draft: '配置草案已保存，尚未进入执行流程。',
  resolving: '正在根据你的配置生成执行计划。',
  proposed: '方案已生成，等待校验。',
  validating: '正在校验配置和权限。',
  preflighting: '正在进行只读试跑，不会写入业务数据。',
  awaiting_approval: '检查已完成，确认后才会提交变更。',
  committing: '已确认，正在提交业务变更。',
  applied: '变更已提交并记录完成结果。',
  cancelled: '这次变更已取消，未继续提交。',
  rejected: '审批未通过，变更未提交。',
  failed: '变更未完成，可以重新尝试或重新规划。',
  expired: '这份草案已过期，需要重新创建。',
  conflicted: '任务已被更新，请基于最新版本重新规划。',
};

const terminalStatuses = new Set<ChangeSetStatus>([
  'applied', 'cancelled', 'rejected', 'failed', 'expired', 'conflicted',
]);

const checkLabels: Record<string, string> = {
  validation: '配置校验', preflight: '只读试跑', authorization: '权限检查',
  approval: '审批/确认', conflict: '版本检查', commit: '提交结果', restore: '恢复结果',
};

function text(value: unknown): string | undefined {
  return typeof value === 'string' && value.trim() ? value.trim() : undefined;
}

function formatTime(value?: string | null): string {
  if (!value) return '';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? '' : date.toLocaleString('zh-CN', {
    month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit',
  });
}

function safeEventLabel(event: ChangeEvent): string {
  return text(event.to_status && statusLabels[event.to_status as ChangeSetStatus])
    || text(event.event_type)
    || '状态更新';
}

function genericFields(changeSet: ChangeSet): ChangeSetDisplayField[] {
  return Object.entries(changeSet.proposed_snapshot)
    .filter(([key]) => isDisplayableKey(key))
    .slice(0, 12)
    .map(([key, value]) => ({ label: key, value: displayValue(value) }));
}

function genericDiff(changeSet: ChangeSet): ChangeSetDiffEntry[] {
  const diff = changeSet.diff as Record<string, unknown>;
  const items = Array.isArray(diff.items) ? diff.items : [];
  const itemDiff = items.flatMap((item) => {
    if (!item || typeof item !== 'object') return [];
    const row = item as Record<string, unknown>;
    const label = text(row.label) || text(row.path);
    if (!label || !isDisplayableKey(label)) return [];
    return [{ label, before: displayValue(row.before), after: displayValue(row.after ?? row.value) }];
  });
  if (itemDiff.length) return itemDiff;

  const patchDiff = changeSet.patch.flatMap((item) => {
    const path = text(item.path) || text(item.key);
    if (!path || !isDisplayableKey(path)) return [];
    return [{
      label: path,
      before: displayValue(item.from),
      after: displayValue(item.value),
    }];
  });
  if (patchDiff.length) return patchDiff;

  const keys = new Set([
    ...Object.keys(changeSet.base_snapshot),
    ...Object.keys(changeSet.proposed_snapshot),
  ]);
  return [...keys].filter((key) => isDisplayableKey(key))
    .filter((key) => JSON.stringify(changeSet.base_snapshot[key]) !== JSON.stringify(changeSet.proposed_snapshot[key]))
    .slice(0, 20)
    .map((key) => ({
      label: key,
      before: displayValue(changeSet.base_snapshot[key]),
      after: displayValue(changeSet.proposed_snapshot[key]),
    }));
}

function genericPlanSteps(changeSet: ChangeSet): string[] {
  const steps = changeSet.plan_snapshot?.steps;
  if (!Array.isArray(steps)) return [];
  return steps.flatMap((step) => {
    if (!step || typeof step !== 'object') return [];
    const item = step as Record<string, unknown>;
    const description = text(item.intent) || text(item.summary) || text(item.description);
    return description ? [description] : [];
  });
}

function getRiskLabel(risk: ChangeSet['risk_level']): string {
  return ({ low: '低风险', medium: '中风险', high: '高风险', critical: '高风险' })[risk];
}

function isVersionConflict(error: unknown): boolean {
  if (error instanceof ApiRequestError) return error.status === 409;
  return typeof error === 'object' && error !== null && 'status' in error
    && (error as { status?: unknown }).status === 409;
}

function Section({ title, children, defaultOpen = true }: { title: string; children: ReactNode; defaultOpen?: boolean }) {
  return (
    <details open={defaultOpen} className="border-t border-border-default/70 py-3 first:border-t-0">
      <summary className="flex cursor-pointer list-none items-center justify-between gap-2 text-sm font-medium text-text-primary [&::-webkit-details-marker]:hidden">
        {title}<ChevronDown className="h-4 w-4 shrink-0 text-text-tertiary" aria-hidden="true" />
      </summary>
      <div className="mt-3 min-w-0">{children}</div>
    </details>
  );
}

function Timeline({ current, events }: { current: ChangeSetStatus; events: ChangeEvent[] }) {
  const visibleEvents = [...events].sort((a, b) => a.sequence - b.sequence);
  if (!visibleEvents.length) {
    return <p className="text-xs text-text-secondary">当前状态：{statusLabels[current]}</p>;
  }
  return (
    <ol className="space-y-2" aria-label="变更状态时间线">
      {visibleEvents.map((event) => {
        const status = event.to_status as ChangeSetStatus | null | undefined;
        return (
          <li key={event.id} className="flex min-w-0 items-start gap-2 text-xs">
            <span className={cn('mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center rounded-full',
              status === current ? 'bg-accent text-text-on-accent' : 'bg-active text-text-tertiary')}>
              {status === current ? <Check className="h-3 w-3" aria-hidden="true" /> : <span className="h-1.5 w-1.5 rounded-full bg-current" />}
            </span>
            <span className="min-w-0 flex-1 break-words text-text-secondary">{safeEventLabel(event)}</span>
            <time className="shrink-0 text-text-tertiary" dateTime={event.created_at}>{formatTime(event.created_at)}</time>
          </li>
        );
      })}
    </ol>
  );
}

function ChangeSummary({ changeSet, adapter }: { changeSet: ChangeSet; adapter?: ChangeSetResourceAdapter }) {
  const fields = adapter?.getFields?.(changeSet) || genericFields(changeSet);
  if (!fields.length) return <p className="text-sm text-text-secondary">暂无可展示的配置摘要。</p>;
  return (
    <dl className="grid min-w-0 grid-cols-1 gap-2 sm:grid-cols-2">
      {fields.map((field) => (
        <div key={field.label} className="min-w-0 rounded-[var(--s-radius-control)] bg-bg-subtle/50 px-3 py-2">
          <dt className="text-[11px] text-text-tertiary">{field.label}</dt>
          <dd className="mt-0.5 break-words text-xs text-text-primary">{field.value}</dd>
        </div>
      ))}
    </dl>
  );
}

function DiffView({ entries }: { entries: ChangeSetDiffEntry[] }) {
  if (!entries.length) return <p className="text-xs text-text-secondary">没有检测到字段变化。</p>;
  return (
    <div className="overflow-x-auto rounded-[var(--s-radius-control)] border border-border-default/70">
      <table className="w-full min-w-[420px] table-fixed text-left text-xs">
        <thead className="bg-bg-subtle/60 text-text-tertiary"><tr><th className="w-1/4 px-3 py-2 font-medium">字段</th><th className="w-[37.5%] px-3 py-2 font-medium">原值</th><th className="w-[37.5%] px-3 py-2 font-medium">新值</th></tr></thead>
        <tbody>{entries.map((entry) => <tr key={`${entry.label}-${entry.before}-${entry.after}`} className="border-t border-border-default/60 align-top"><td className="break-words px-3 py-2 text-text-secondary">{entry.label}</td><td className="break-words px-3 py-2 text-text-tertiary">{entry.before}</td><td className="break-words px-3 py-2 text-text-primary">{entry.after}</td></tr>)}</tbody>
      </table>
    </div>
  );
}

function Checks({ checks, nonExecutionPreflightLabel }: {
  checks: ChangeCheck[];
  nonExecutionPreflightLabel?: string;
}) {
  if (!checks.length) return <p className="text-xs text-text-secondary">尚未产生检查结果。</p>;
  return (
    <ul className="space-y-2" aria-label="校验和试跑结果">
      {checks.map((check) => {
        const passed = check.status === 'passed';
        const failed = check.status === 'failed';
        return <li key={check.id} className="flex min-w-0 items-center gap-2 text-xs">
          {passed ? <CheckCircle2 className="h-4 w-4 shrink-0 text-success" aria-hidden="true" /> : failed ? <XCircle className="h-4 w-4 shrink-0 text-error" aria-hidden="true" /> : <Clock3 className="h-4 w-4 shrink-0 text-text-tertiary" aria-hidden="true" />}
          <span className="min-w-0 flex-1 break-words text-text-secondary">{
            check.check_type === 'preflight' && nonExecutionPreflightLabel
              ? nonExecutionPreflightLabel
              : checkLabels[check.check_type] || '流程检查'
          }</span>
          <span className={cn('shrink-0', passed ? 'text-success' : failed ? 'text-error' : 'text-text-tertiary')}>{passed ? '通过' : failed ? '未通过' : check.status === 'running' ? '进行中' : '待执行'}</span>
        </li>;
      })}
    </ul>
  );
}

export default function ChangeSetCard({
  changeSetId,
  fallbackTitle,
  resourceType,
  adapter: providedAdapter,
  actionHandlers,
  onChangeSetUpdated,
}: ChangeSetCardProps) {
  const [activeId, setActiveId] = useState(changeSetId);
  const [changeSet, setChangeSet] = useState<ChangeSet | null>(null);
  const [events, setEvents] = useState<ChangeEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState(false);
  const [pendingAction, setPendingAction] = useState<ChangeSetAction | null>(null);
  const [actionNotice, setActionNotice] = useState('');

  useEffect(() => {
    setActiveId(changeSetId);
  }, [changeSetId]);

  const load = useCallback(async () => {
    if (!activeId) return;
    setLoading(true);
    setLoadError(false);
    try {
      const next = await changeSetService.get(activeId);
      setChangeSet(next);
      onChangeSetUpdated?.(next);
      setActionNotice('');
      try {
        const timeline = await changeSetService.timeline(activeId);
        setEvents(timeline.events || []);
      } catch {
        setEvents([]);
      }
    } catch {
      setLoadError(true);
    } finally {
      setLoading(false);
    }
  }, [activeId, onChangeSetUpdated]);

  useEffect(() => { void load(); }, [load]);

  useEffect(() => {
    const onUpdated = (event: Event) => {
      const detail = (event as CustomEvent<{ changeSetId?: string; change_set_id?: string }>).detail;
      const id = detail?.changeSetId || detail?.change_set_id;
      if (id === activeId) void load();
    };
    window.addEventListener(CHANGESET_UPDATED_EVENT, onUpdated);
    return () => window.removeEventListener(CHANGESET_UPDATED_EVENT, onUpdated);
  }, [activeId, load]);

  const adapter = useMemo(
    () => providedAdapter || (changeSet ? getChangeSetResourceAdapter(changeSet) : undefined),
    [changeSet, providedAdapter],
  );

  const handleAction = useCallback(async (action: ChangeSetAction) => {
    if (!changeSet || pendingAction) return;
    setPendingAction(action);
    setActionNotice('');
    try {
      let result: ChangeSet | void;
      if (actionHandlers?.[action]) result = await actionHandlers[action]!(changeSet);
      else if (action === 'cancel') result = await changeSetService.cancel(changeSet.id, '用户取消');
      else if (action === 'retry') result = await changeSetService.recover(changeSet.id);
      else if (action === 'confirm') result = await changeSetService.confirm(changeSet.id);
      else {
        dispatchChangeSetAction({ action, changeSetId: changeSet.id, revision: changeSet.revision });
        setActionNotice('操作请求已发送，等待最新状态。');
        return;
      }
      if (result?.id && result.id !== activeId) setActiveId(result.id);
      if (result) {
        setChangeSet(result);
        onChangeSetUpdated?.(result);
      }
    } catch (error) {
      if (isVersionConflict(error)) {
        await load();
        setActionNotice('任务已被更新，请基于最新版本重新规划。');
      } else {
        setActionNotice('操作未完成，请稍后重试。');
      }
    } finally {
      setPendingAction(null);
    }
  }, [actionHandlers, activeId, changeSet, load, onChangeSetUpdated, pendingAction]);

  if (loading && !changeSet) {
    return <div className={`${MESSAGE_CONTENT_LAYOUT.fill} my-3 rounded-[var(--s-radius-card)] border border-border-default bg-surface p-4`} role="status" aria-live="polite"><Loader2 className="h-4 w-4 animate-spin text-accent" aria-hidden="true" /><span className="sr-only">正在读取变更状态</span></div>;
  }
  if (loadError && !changeSet) {
    return <div className={`${MESSAGE_CONTENT_LAYOUT.fill} my-3 rounded-[var(--s-radius-card)] border border-error/30 bg-error-light p-4 text-sm text-error`} role="alert"><p>暂时无法读取变更状态。</p><Button className="mt-3" variant="secondary" size="sm" onClick={() => void load()} icon={<RefreshCw className="h-3.5 w-3.5" />}>重新读取</Button></div>;
  }
  if (!changeSet) return null;

  const status = changeSet.status;
  const diff = adapter?.getDiff?.(changeSet) || genericDiff(changeSet);
  const planSteps = adapter?.getPlanSteps?.(changeSet) || genericPlanSteps(changeSet);
  const title = adapter?.getTitle?.(changeSet) || fallbackTitle || '变更方案';
  const summary = adapter?.getSummary?.(changeSet);
  const presentation = adapter?.getPresentation?.(changeSet);
  const canCancel = !terminalStatuses.has(status);
  const isConflict = status === 'conflicted';
  const hasApproval = status === 'awaiting_approval';
  const canRetry = status === 'failed';
  const canReplan = isConflict || status === 'rejected' || status === 'expired';
  const hasActions = canCancel || hasApproval || canRetry || canReplan;
  const approvalImpact = changeSet.policy_snapshot;
  const policyText = typeof approvalImpact.requires_approval === 'boolean'
    ? (approvalImpact.requires_approval ? '需要确认后提交' : '无需额外审批')
    : '已按当前组织策略检查';
  const actionLabel = (action: ChangeSetAction) => ({
    confirm: '确认提交', cancel: '取消变更', retry: '重新尝试',
    replan: '重新规划', resolve_conflict: '解决冲突',
  }[action]);

  return (
    <section className={`${MESSAGE_CONTENT_LAYOUT.fill} my-3 min-w-0 overflow-hidden rounded-[var(--s-radius-card)] border border-border-default bg-surface shadow-sm`} aria-label={`${title}变更流程`}>
      <header className="flex min-w-0 items-start gap-3 border-b border-border-default bg-surface-elevated px-4 py-3">
        <span className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-accent/10 text-accent"><GitCompare className="h-4 w-4" aria-hidden="true" /></span>
        <div className="min-w-0 flex-1"><h3 className="break-words text-sm font-semibold text-text-primary">{title}</h3><p className="mt-1 break-words text-xs text-text-secondary">{summary || statusDescriptions[status]}</p></div>
        <span className={cn('shrink-0 rounded-full px-2 py-1 text-[11px] font-medium', status === 'applied' ? 'bg-success/10 text-success' : status === 'failed' || isConflict || status === 'rejected' ? 'bg-error-light text-error' : 'bg-active text-text-secondary')} aria-label={`当前状态：${statusLabels[status]}`}>{statusLabels[status]}</span>
      </header>

      {presentation?.notice && (
        <div className={cn(
          'mx-4 mt-3 rounded-[var(--s-radius-control)] px-3 py-2 text-xs',
          presentation.mode === 'destructive' ? 'bg-error-light text-error' : 'bg-warning/10 text-warning',
        )} role="alert">
          {presentation.notice}
        </div>
      )}

      <div className="min-w-0 px-4">
        <Section title="状态时间线"><Timeline current={status} events={events} /></Section>
        <Section title={presentation?.summaryTitle || '变更摘要'}><ChangeSummary changeSet={changeSet} adapter={adapter} /></Section>
        {presentation?.showDiff !== false && <Section title={`${presentation?.diffTitle || 'Diff'}${diff.length ? ` · ${diff.length} 项` : ''}`}><DiffView entries={diff} /></Section>}
        <Section title="风险与权限影响"><div className="flex flex-wrap gap-2 text-xs"><span className="inline-flex items-center gap-1 rounded-full bg-warning/10 px-2 py-1 text-warning"><AlertTriangle className="h-3.5 w-3.5" aria-hidden="true" />{getRiskLabel(changeSet.risk_level)}</span><span className="inline-flex items-center gap-1 rounded-full bg-active px-2 py-1 text-text-secondary"><ShieldCheck className="h-3.5 w-3.5" aria-hidden="true" />{policyText}</span></div></Section>
        {presentation?.showPlan !== false && <Section title="AI 规划的执行路径"><ol className="list-decimal space-y-1.5 pl-5 text-xs leading-5 text-text-secondary">{planSteps.length ? planSteps.map((step) => <li key={step} className="break-words">{step}</li>) : <li className="list-none pl-0">执行路径将在规划完成后显示。</li>}</ol></Section>}
        <Section title={presentation?.checksTitle || '校验与只读试跑'}><Checks checks={changeSet.checks} nonExecutionPreflightLabel={presentation?.nonExecutionPreflightLabel} /></Section>
      </div>

      {(isConflict || status === 'failed' || status === 'rejected' || status === 'expired' || status === 'cancelled' || status === 'applied') && (
        <div className={cn('mx-4 mb-3 rounded-[var(--s-radius-control)] px-3 py-2 text-xs', status === 'applied' ? 'bg-success/10 text-success' : isConflict ? 'bg-warning/10 text-warning' : status === 'cancelled' ? 'bg-active text-text-secondary' : 'bg-error-light text-error')} role={status === 'applied' || status === 'cancelled' ? 'status' : 'alert'}>
          {isConflict ? '任务已被更新，请基于最新版本重新规划。' : statusDescriptions[status]}
        </div>
      )}

      {hasActions && <footer className="flex flex-wrap items-center gap-2 border-t border-border-default bg-surface-elevated px-4 py-3">
        {hasApproval && <Button size="sm" variant={presentation?.mode === 'destructive' ? 'danger' : 'accent'} onClick={() => void handleAction('confirm')} loading={pendingAction === 'confirm'} disabled={!!pendingAction} icon={<CheckCircle2 className="h-3.5 w-3.5" />}>{presentation?.confirmationLabel || actionLabel('confirm')}</Button>}
        {canRetry && <Button size="sm" onClick={() => void handleAction('retry')} loading={pendingAction === 'retry'} disabled={!!pendingAction} icon={<RefreshCw className="h-3.5 w-3.5" />}>{actionLabel('retry')}</Button>}
        {canReplan && <Button size="sm" onClick={() => void handleAction('replan')} loading={pendingAction === 'replan'} disabled={!!pendingAction} icon={<RefreshCw className="h-3.5 w-3.5" />}>{actionLabel('replan')}</Button>}
        {isConflict && <Button size="sm" variant="secondary" onClick={() => void handleAction('resolve_conflict')} loading={pendingAction === 'resolve_conflict'} disabled={!!pendingAction}>{actionLabel('resolve_conflict')}</Button>}
        {canCancel && <Button size="sm" variant="secondary" onClick={() => void handleAction('cancel')} loading={pendingAction === 'cancel'} disabled={!!pendingAction} icon={<X className="h-3.5 w-3.5" />}>{presentation?.cancellationLabel || actionLabel('cancel')}</Button>}
        {actionNotice && <span className="basis-full break-words text-xs text-text-secondary" role="status" aria-live="polite">{actionNotice}</span>}
      </footer>}
      {!hasActions && <div className="flex items-center gap-2 border-t border-border-default bg-surface-elevated px-4 py-3 text-xs text-text-tertiary"><CheckCircle2 className="h-4 w-4" aria-hidden="true" />流程已结束</div>}
      <span className="sr-only">ChangeSet ID: {activeId}{resourceType ? `，资源类型：${resourceType}` : ''}</span>
    </section>
  );
}
