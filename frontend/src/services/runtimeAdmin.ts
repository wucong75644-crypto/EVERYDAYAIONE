import { request } from './api';

export type RuntimeState = 'ready' | 'degraded' | 'unavailable' | 'disabled';

export interface RuntimeDomainStatus {
  state: RuntimeState;
  summary: Record<string, unknown>;
  error_code?: string;
}

export interface RuntimeStatusSnapshot {
  schema_version: number;
  tenant_id: string;
  composition: RuntimeDomainStatus;
  workers: RuntimeDomainStatus;
  tenant_control: RuntimeDomainStatus;
  owner_transition: RuntimeDomainStatus;
  claim_gate: RuntimeDomainStatus;
  production: RuntimeDomainStatus;
  provider: RuntimeDomainStatus;
  submissions: RuntimeDomainStatus;
  scheduler: RuntimeDomainStatus;
  artifact: RuntimeDomainStatus;
  workspace: RuntimeDomainStatus;
  child_run: RuntimeDomainStatus;
  projection: RuntimeDomainStatus;
  cost: RuntimeDomainStatus;
  sandbox: RuntimeDomainStatus;
  capabilities: Record<string, RuntimeDomainStatus>;
  failure_closed_reasons: string[];
}

export interface RuntimeProviderOperation {
  submission_id?: string;
  provider?: string;
  capability?: string | null;
  state?: string;
  created_at?: string;
  age_seconds?: number;
  state_version?: number;
  cancel_requested?: boolean;
  reason_code?: string | null;
  fence?: Record<string, unknown>;
}

export interface RuntimeRecoveryItem {
  recovery_domain?: string;
  target_id?: string;
  state?: string;
  reason_code?: string | null;
  recovery_required?: boolean;
  cleanup_required?: boolean;
  fence?: Record<string, unknown>;
  created_at?: string;
}

export interface RuntimeAdminSnapshot {
  status: RuntimeStatusSnapshot;
  providerOperations: RuntimeProviderOperation[];
  recovery: RuntimeRecoveryItem[];
  costSideEffects: Record<string, unknown>;
}

interface Envelope<T> {
  success: boolean;
  data: T;
  snapshot?: RuntimeStatusSnapshot;
}

function readIdempotencyKey(orgId: string): string {
  return `runtime-admin-read:${orgId}`;
}

export async function getRuntimeAdminSnapshot(
  orgId: string,
  signal?: AbortSignal,
): Promise<RuntimeAdminSnapshot> {
  const [status, providerOperations, recovery, costSideEffects] = await Promise.all([
    request<Envelope<Record<string, unknown>>>({
      method: 'GET',
      url: '/admin/agent-runtime/status',
      params: { org_id: orgId },
      headers: { 'Idempotency-Key': readIdempotencyKey(orgId) },
      signal,
    }),
    request<Envelope<{ items?: RuntimeProviderOperation[] }>>({
      method: 'GET',
      url: '/admin/agent-runtime/provider-operations',
      params: { org_id: orgId, limit: 20 },
      signal,
    }),
    request<Envelope<{ items?: RuntimeRecoveryItem[] }>>({
      method: 'GET',
      url: '/admin/agent-runtime/recovery',
      params: { org_id: orgId, limit: 20 },
      signal,
    }),
    request<Envelope<Record<string, unknown>>>({
      method: 'GET',
      url: '/admin/agent-runtime/cost-side-effects',
      params: { org_id: orgId, limit: 20 },
      signal,
    }),
  ]);

  if (!status.snapshot) {
    throw new Error('RUNTIME_ADMIN_STATUS_UNAVAILABLE');
  }
  return {
    status: status.snapshot,
    providerOperations: providerOperations.data?.items ?? [],
    recovery: recovery.data?.items ?? [],
    costSideEffects: costSideEffects.data ?? {},
  };
}
