import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import AdminPanel from '../AdminPanel';

vi.mock('../../../stores/useAuthStore', () => ({
  useAuthStore: () => ({ user: { role: 'user' }, currentOrg: null }),
}));
vi.mock('../SuperAdminPanel', () => ({ default: () => <div>platform</div> }));
vi.mock('../OrgManagePanel', () => ({ default: () => <div>org</div> }));
vi.mock('../../integrations/KuaimaiIntegrationPanel', () => ({ default: () => <div>kuaimai</div> }));

describe('AdminPanel Runtime visibility', () => {
  it('does not expose Runtime operations to non-super-admin users', () => {
    render(<AdminPanel />);
    expect(screen.queryByText('Runtime 运维')).not.toBeInTheDocument();
    expect(screen.getByText('无管理权限')).toBeInTheDocument();
  });
});
