import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import RuntimeAdminPanel from './RuntimeAdminPanel';
import { getRuntimeAdminSnapshot } from '../../../services/runtimeAdmin';
import { listAllOrgs } from '../../../services/org';

vi.mock('../../../services/runtimeAdmin', () => ({ getRuntimeAdminSnapshot: vi.fn() }));
vi.mock('../../../services/org', () => ({ listAllOrgs: vi.fn() }));
vi.mock('../../../stores/useAuthStore', () => ({
  useAuthStore: (selector: (state: { currentOrgId: string | null }) => unknown) => selector({ currentOrgId: 'org-a' }),
}));

const mockedSnapshot = vi.mocked(getRuntimeAdminSnapshot);
const mockedOrgs = vi.mocked(listAllOrgs);

function snapshot(tenantId: string) {
  const domain = { state: 'ready' as const, summary: {} };
  return {
    status: {
      schema_version: 1, tenant_id: tenantId,
      composition: domain, workers: domain, tenant_control: { ...domain, summary: { kill_epoch: 2 } },
      owner_transition: domain, claim_gate: domain, production: domain, provider: domain,
      submissions: { ...domain, summary: { accepted: 1, unknown: 2, reconcile_required: 3 } },
      scheduler: domain, artifact: domain, workspace: domain, child_run: domain,
      projection: { ...domain, summary: { backlog: 1, dead: 0 } }, cost: domain, sandbox: domain,
      capabilities: {}, failure_closed_reasons: [],
    },
    providerOperations: [{ provider: 'demo', state: 'unknown', capability: 'read' }],
    recovery: [{ recovery_domain: 'sandbox', state: 'pending' }],
    costSideEffects: {
      cost_ledger: [], side_effect_ledger: [], production_ready: false,
      execution_token: 'must-not-render', provider_payload: 'must-not-render',
    },
  };
}

describe('RuntimeAdminPanel', () => {
  beforeEach(() => {
    mockedOrgs.mockResolvedValue([
      { id: 'org-a', name: '企业 A', status: 'active', owner_id: 'owner-a', created_at: '' },
      { id: 'org-b', name: '企业 B', status: 'active', owner_id: 'owner-b', created_at: '' },
    ]);
    mockedSnapshot.mockReset();
  });

  it('shows an explicit empty state when no enterprise is available', async () => {
    mockedOrgs.mockResolvedValueOnce([]);
    render(<RuntimeAdminPanel />);
    expect(await screen.findByText('暂无可展示的 Runtime 状态')).toBeInTheDocument();
    expect(mockedSnapshot).not.toHaveBeenCalled();
  });

  it('shows failure state and never renders sensitive fields', async () => {
    mockedSnapshot.mockResolvedValue(snapshot('org-a'));
    render(<RuntimeAdminPanel />);
    expect(await screen.findByText('Runtime 运维')).toBeInTheDocument();
    expect(await screen.findByText('demo')).toBeInTheDocument();
    expect(screen.queryByText('must-not-render')).not.toBeInTheDocument();
  });

  it('shows an explicit error state when the read API fails', async () => {
    mockedSnapshot.mockRejectedValue(new Error('network down'));
    render(<RuntimeAdminPanel />);
    expect(await screen.findByRole('alert')).toHaveTextContent('Runtime 运维数据暂时不可用');
  });

  it('renders tenant gate blocked state and aggregates recovery by domain', async () => {
    const blocked = snapshot('org-a');
    blocked.status.tenant_control = {
      state: 'degraded', summary: { gate_blocked: true, kill_epoch: 9, state_version: 12 },
    };
    mockedSnapshot.mockResolvedValue(blocked);
    render(<RuntimeAdminPanel />);
    expect(await screen.findByText('已阻断')).toBeInTheDocument();
    expect(screen.getByText('9')).toBeInTheDocument();
    expect(screen.getByText('12')).toBeInTheDocument();
    expect(screen.getByTestId('recovery-count-sandbox')).toHaveTextContent('1');
  });

  it('ignores stale tenant responses after switching enterprise', async () => {
    let resolveA!: (value: ReturnType<typeof snapshot>) => void;
    const first = new Promise<ReturnType<typeof snapshot>>((resolve) => { resolveA = resolve; });
    mockedSnapshot.mockReturnValueOnce(first).mockResolvedValueOnce(snapshot('org-b'));
    render(<RuntimeAdminPanel />);

    const selector = await screen.findByRole('combobox', { name: '选择企业' });
    await userEvent.selectOptions(selector, 'org-b');
    expect(await screen.findByText('数据范围：org-b')).toBeInTheDocument();
    resolveA(snapshot('org-a'));
    await waitFor(() => expect(screen.queryByText('org-a')).not.toBeInTheDocument());
  });
});
