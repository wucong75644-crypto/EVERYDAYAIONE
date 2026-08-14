import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../../../services/org', () => ({
  listAllOrgs: vi.fn(),
  createOrg: vi.fn(),
  searchUser: vi.fn(),
  suspendOrg: vi.fn(),
  restoreOrg: vi.fn(),
}));

import {
  listAllOrgs, restoreOrg, suspendOrg,
} from '../../../services/org';
import SuperAdminPanel from '../SuperAdminPanel';

const activeOrg = {
  id: 'org-active',
  name: '运行企业',
  status: 'active' as const,
  owner_id: 'owner-1',
  created_at: '2026-07-27T00:00:00Z',
  member_count: 2,
};
const suspendedOrg = {
  ...activeOrg,
  id: 'org-suspended',
  name: '停用企业',
  status: 'suspended' as const,
};

describe('SuperAdminPanel lifecycle controls', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(listAllOrgs).mockResolvedValue([activeOrg, suspendedOrg]);
    vi.mocked(suspendOrg).mockResolvedValue({
      success: true,
      data: { ...activeOrg, status: 'suspended' },
    });
    vi.mocked(restoreOrg).mockResolvedValue({
      success: true,
      data: { ...suspendedOrg, status: 'active' },
    });
  });

  it('shows exactly one status-appropriate action and no delete action', async () => {
    render(<SuperAdminPanel />);

    expect(await screen.findByRole('button', { name: '停用' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '恢复' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /删除企业/ })).not.toBeInTheDocument();
  });

  it('requires the exact organization name and reloads after suspension', async () => {
    render(<SuperAdminPanel />);
    fireEvent.click(await screen.findByRole('button', { name: '停用' }));

    const confirm = screen.getByRole('button', { name: '确认停用' });
    expect(confirm).toBeDisabled();
    fireEvent.change(screen.getByLabelText('输入完整企业名称以确认'), {
      target: { value: '运行企业' },
    });
    expect(confirm).toBeEnabled();
    fireEvent.click(confirm);

    await waitFor(() => expect(suspendOrg).toHaveBeenCalledOnce());
    await waitFor(() => expect(listAllOrgs).toHaveBeenCalledTimes(2));
  });

  it('uses a second confirmation and reloads after restoration', async () => {
    render(<SuperAdminPanel />);
    fireEvent.click(await screen.findByRole('button', { name: '恢复' }));
    fireEvent.click(screen.getByRole('button', { name: '确认恢复' }));

    await waitFor(() => expect(restoreOrg).toHaveBeenCalledOnce());
    await waitFor(() => expect(listAllOrgs).toHaveBeenCalledTimes(2));
  });

  it('aborts the active list request on unmount', async () => {
    const observedSignals: AbortSignal[] = [];
    vi.mocked(listAllOrgs).mockImplementation(async (signal) => {
      if (signal) observedSignals.push(signal);
      return [activeOrg, suspendedOrg];
    });

    const view = render(<SuperAdminPanel />);
    await screen.findByRole('button', { name: '停用' });
    view.unmount();

    expect(observedSignals[0].aborted).toBe(true);
  });
});
