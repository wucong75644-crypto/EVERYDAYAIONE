import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../api', () => ({ request: vi.fn() }));

import { request } from '../api';
import { listAllOrgs, restoreOrg, suspendOrg } from '../org';

const mockRequest = vi.mocked(request);

describe('organization lifecycle service', () => {
  beforeEach(() => vi.clearAllMocks());

  it('loads the authoritative list with cancellation support', async () => {
    const controller = new AbortController();
    mockRequest.mockResolvedValue([]);

    await listAllOrgs(controller.signal);

    expect(mockRequest).toHaveBeenCalledWith({
      method: 'GET',
      url: '/org/admin/all',
      signal: controller.signal,
    });
  });

  it.each([
    ['suspend', suspendOrg],
    ['restore', restoreOrg],
  ] as const)('posts the %s transition without optimistic state', async (action, call) => {
    const controller = new AbortController();
    mockRequest.mockResolvedValue({
      success: true,
      data: { id: 'org-1', status: action === 'suspend' ? 'suspended' : 'active' },
    });

    await call('org-1', controller.signal);

    expect(mockRequest).toHaveBeenCalledWith({
      method: 'POST',
      url: `/org/admin/org-1/${action}`,
      signal: controller.signal,
    });
  });
});
