import { beforeEach, describe, expect, it, vi } from 'vitest';
import { getRuntimeAdminSnapshot } from '../runtimeAdmin';
import { request } from '../api';

vi.mock('../api', () => ({ request: vi.fn() }));

const mockedRequest = vi.mocked(request);

describe('runtime admin read service', () => {
  beforeEach(() => mockedRequest.mockReset());

  it('loads only the four read endpoints for the selected tenant', async () => {
    mockedRequest
      .mockResolvedValueOnce({ snapshot: { tenant_id: 'org-a' } })
      .mockResolvedValueOnce({ data: { items: [{ state: 'unknown' }] } })
      .mockResolvedValueOnce({ data: { items: [{ recovery_domain: 'sandbox' }] } })
      .mockResolvedValueOnce({ data: { cost_ledger: [], side_effect_ledger: [] } });

    const signal = new AbortController().signal;
    const result = await getRuntimeAdminSnapshot('org-a', signal);

    expect(result.status.tenant_id).toBe('org-a');
    expect(result.providerOperations).toHaveLength(1);
    expect(mockedRequest).toHaveBeenCalledTimes(4);
    expect(mockedRequest.mock.calls.map(([config]) => config.method)).toEqual(['GET', 'GET', 'GET', 'GET']);
    expect(mockedRequest.mock.calls.every(([config]) => config.params?.org_id === 'org-a')).toBe(true);
    expect(mockedRequest.mock.calls.some(([config]) => config.url?.includes('/requeue'))).toBe(false);
  });

  it('fails closed when the status response lacks the redacted snapshot', async () => {
    mockedRequest
      .mockResolvedValueOnce({ data: {} })
      .mockResolvedValueOnce({ data: { items: [] } })
      .mockResolvedValueOnce({ data: { items: [] } })
      .mockResolvedValueOnce({ data: {} });

    await expect(getRuntimeAdminSnapshot('org-a')).rejects.toThrow('RUNTIME_ADMIN_STATUS_UNAVAILABLE');
  });
});
