import { useEffect, useRef, useState, type ReactNode } from 'react';
import { toApiRequestError } from '../../../services/api';
import {
  getRuntimeAdminSnapshot,
  type RuntimeAdminSnapshot,
  type RuntimeDomainStatus,
  type RuntimeState,
} from '../../../services/runtimeAdmin';
import { listAllOrgs, type OrgDetail } from '../../../services/org';
import { useAuthStore } from '../../../stores/useAuthStore';

const STATE_LABELS: Record<RuntimeState, string> = {
  ready: '就绪', degraded: '降级', unavailable: '不可用', disabled: '已禁用',
};

function stateClass(state: RuntimeState): string {
  return {
    ready: 'bg-success-light text-success',
    degraded: 'bg-warning-light text-warning',
    unavailable: 'bg-error-light text-error',
    disabled: 'bg-hover text-text-tertiary',
  }[state];
}

function StateBadge({ status }: { status: RuntimeDomainStatus }) {
  return (
    <span className={`inline-flex rounded-full px-2 py-0.5 text-xs font-medium ${stateClass(status.state)}`}>
      {STATE_LABELS[status.state]}
    </span>
  );
}

function Metric({ label, value }: { label: string; value: unknown }) {
  return (
    <div className="rounded-lg bg-hover/60 px-3 py-2">
      <div className="text-xs text-text-tertiary">{label}</div>
      <div className="mt-1 text-lg font-semibold text-text-primary">{String(value ?? '—')}</div>
    </div>
  );
}

function Section({ title, status, children }: {
  title: string;
  status?: RuntimeDomainStatus;
  children: ReactNode;
}) {
  return (
    <section className="rounded-xl border border-[var(--s-border-default)] bg-surface-card p-4">
      <div className="mb-3 flex items-center justify-between gap-3">
        <h3 className="text-sm font-medium text-text-primary">{title}</h3>
        {status && <StateBadge status={status} />}
      </div>
      {children}
    </section>
  );
}

function safeError(error: unknown): string {
  const parsed = toApiRequestError(error);
  return parsed.code === '403' || parsed.status === 403
    ? '当前账号没有 Runtime 运维查看权限'
    : 'Runtime 运维数据暂时不可用';
}

function countByState(items: Array<{ state?: string }>): Record<string, number> {
  return items.reduce<Record<string, number>>((counts, item) => {
    const state = item.state || 'unknown';
    counts[state] = (counts[state] || 0) + 1;
    return counts;
  }, {});
}

function RuntimeSnapshotView({ snapshot }: { snapshot: RuntimeAdminSnapshot }) {
  const { status } = snapshot;
  const control = status.tenant_control.summary;
  const projection = status.projection.summary;
  const unknown = status.submissions.summary;
  const operationCounts = countByState(snapshot.providerOperations);
  const recoveryCounts = countByState(snapshot.recovery);

  return (
    <div className="space-y-4">
      <p className="text-xs text-text-tertiary">数据范围：{status.tenant_id}</p>
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <Metric label="租户 Kill epoch" value={control.kill_epoch} />
        <Metric label="Provider Kill epoch" value={control.provider_kill_epoch} />
        <Metric label="Capability Kill epoch" value={control.capability_kill_epoch} />
        <Metric label="Projection backlog / dead" value={`${projection.backlog ?? 0} / ${projection.dead ?? 0}`} />
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Section title="Runtime 健康与就绪" status={status.production}>
          <div className="grid grid-cols-2 gap-2 text-sm">
            <div className="flex items-center justify-between"><span>Composition</span><StateBadge status={status.composition} /></div>
            <div className="flex items-center justify-between"><span>Workers</span><StateBadge status={status.workers} /></div>
            <div className="flex items-center justify-between"><span>Claim gate</span><StateBadge status={status.claim_gate} /></div>
            <div className="flex items-center justify-between"><span>Projection</span><StateBadge status={status.projection} /></div>
          </div>
          {status.failure_closed_reasons.length > 0 && (
            <p className="mt-3 text-xs text-warning">未就绪原因：{status.failure_closed_reasons.join('、')}</p>
          )}
        </Section>

        <Section title="租户 / Provider / Capability 控制" status={status.tenant_control}>
          <div className="grid grid-cols-2 gap-2 text-sm">
            <Metric label="租户状态" value={control.gate_blocked ? '已阻断' : '允许'} />
            <Metric label="控制版本" value={control.state_version} />
            <Metric label="Provider epoch" value={control.provider_kill_epoch} />
            <Metric label="Capability epoch" value={control.capability_kill_epoch} />
          </div>
        </Section>

        <Section title="Accepted / Unknown / Reconcile required" status={status.submissions}>
          <div className="grid grid-cols-3 gap-2">
            <Metric label="Accepted" value={unknown.accepted ?? operationCounts.accepted ?? 0} />
            <Metric label="Unknown" value={unknown.unknown ?? operationCounts.unknown ?? 0} />
            <Metric label="需恢复" value={unknown.reconcile_required ?? operationCounts.reconcile_required ?? 0} />
          </div>
        </Section>

        <Section title="恢复快照">
          <div className="grid grid-cols-2 gap-2 text-sm">
            {(['artifact', 'workspace', 'scheduler', 'child_run', 'sandbox'] as const).map((domain) => (
              <div key={domain} className="flex justify-between rounded bg-hover/60 px-3 py-2">
                <span>{domain}</span><span className="font-medium">{recoveryCounts[domain] ?? 0}</span>
              </div>
            ))}
          </div>
        </Section>

        <Section title="成本 / 副作用摘要" status={status.cost}>
          <div className="grid grid-cols-2 gap-2">
            <Metric label="成本状态数" value={Array.isArray(snapshot.costSideEffects.cost_ledger) ? snapshot.costSideEffects.cost_ledger.length : 0} />
            <Metric label="副作用状态数" value={Array.isArray(snapshot.costSideEffects.side_effect_ledger) ? snapshot.costSideEffects.side_effect_ledger.length : 0} />
            <Metric label="生产就绪" value={snapshot.costSideEffects.production_ready === true ? '是' : '否'} />
            <Metric label="账本契约" value={snapshot.costSideEffects.currency_contract || '—'} />
          </div>
        </Section>

        <Section title="当前 Provider 运维状态">
          {snapshot.providerOperations.length === 0 ? (
            <p className="text-sm text-text-tertiary">暂无 accepted / unknown / reconcile_required 记录</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-left text-xs">
                <thead className="text-text-tertiary"><tr><th className="py-2">Provider</th><th>状态</th><th>Capability</th><th>创建时间</th></tr></thead>
                <tbody>{snapshot.providerOperations.map((item) => (
                  <tr key={item.submission_id} className="border-t border-[var(--s-border-default)]">
                    <td className="py-2 text-text-primary">{item.provider || '—'}</td>
                    <td>{item.state || '—'}</td><td>{item.capability || '—'}</td><td>{item.created_at || '—'}</td>
                  </tr>
                ))}</tbody>
              </table>
            </div>
          )}
        </Section>
      </div>
    </div>
  );
}

export default function RuntimeAdminPanel() {
  const currentOrgId = useAuthStore((state) => state.currentOrgId);
  const [orgs, setOrgs] = useState<OrgDetail[]>([]);
  const [orgId, setOrgId] = useState(currentOrgId || '');
  const [orgsLoaded, setOrgsLoaded] = useState(false);
  const [snapshot, setSnapshot] = useState<RuntimeAdminSnapshot | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const generation = useRef(0);
  const controller = useRef<AbortController | null>(null);

  useEffect(() => {
    let active = true;
    void listAllOrgs().then((items) => {
      if (!active) return;
      setOrgs(items);
      setOrgId(items.find((item) => item.id === currentOrgId)?.id || items[0]?.id || '');
      setOrgsLoaded(true);
    }).catch((reason) => {
      if (active) setError(safeError(reason));
    });
    return () => { active = false; };
  }, [currentOrgId]);

  useEffect(() => {
    controller.current?.abort();
    if (!orgsLoaded) return;
    if (!orgId) { setSnapshot(null); setLoading(false); return; }
    const nextController = new AbortController();
    controller.current = nextController;
    const requestGeneration = ++generation.current;
    setLoading(true); setError(''); setSnapshot(null);
    void getRuntimeAdminSnapshot(orgId, nextController.signal).then((value) => {
      if (requestGeneration === generation.current && !nextController.signal.aborted) setSnapshot(value);
    }).catch((reason) => {
      if (!nextController.signal.aborted && requestGeneration === generation.current) setError(safeError(reason));
    }).finally(() => {
      if (requestGeneration === generation.current) setLoading(false);
    });
    return () => nextController.abort();
  }, [orgId, orgsLoaded]);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div><h2 className="text-base font-medium text-text-primary">Runtime 运维</h2><p className="text-xs text-text-tertiary">只读状态、恢复与成本观察，不提供任何执行操作。</p></div>
        <label className="flex items-center gap-2 text-sm text-text-secondary">
          企业
          <select aria-label="选择企业" value={orgId} onChange={(event) => setOrgId(event.target.value)} className="rounded-lg border border-[var(--s-border-default)] bg-surface-card px-3 py-2 text-text-primary">
            <option value="" disabled>请选择企业</option>
            {orgs.map((org) => <option key={org.id} value={org.id}>{org.name}</option>)}
          </select>
        </label>
      </div>
      {error && <div role="alert" className="rounded-lg bg-error-light p-3 text-sm text-error">{error}</div>}
      {!error && loading && <div className="rounded-xl border border-[var(--s-border-default)] p-8 text-center text-sm text-text-tertiary">加载 Runtime 状态…</div>}
      {!error && !loading && !snapshot && <div className="rounded-xl border border-[var(--s-border-default)] p-8 text-center text-sm text-text-tertiary">暂无可展示的 Runtime 状态</div>}
      {snapshot && <RuntimeSnapshotView snapshot={snapshot} />}
    </div>
  );
}
