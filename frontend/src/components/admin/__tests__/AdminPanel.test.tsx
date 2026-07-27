import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import AdminPanel from '../AdminPanel';

const authState = {
  user: { role: 'admin' },
  currentOrg: { org_id: 'org-a', role: 'owner' },
};

vi.mock('../../../stores/useAuthStore', () => ({
  useAuthStore: () => authState,
}));
vi.mock('../OrgManagePanel', () => ({
  default: () => <div>企业管理内容</div>,
}));
vi.mock('../../integrations/KuaimaiIntegrationPanel', () => ({
  default: () => <div>快麦接入内容</div>,
}));

describe('AdminPanel 深度链接', () => {
  beforeEach(() => {
    authState.user.role = 'admin';
    authState.currentOrg.role = 'owner';
  });

  it('按 tab=org 打开企业管理', () => {
    render(
      <MemoryRouter initialEntries={['/admin?tab=org&section=organization']}>
        <AdminPanel />
      </MemoryRouter>,
    );

    expect(screen.getByText('企业管理内容')).toBeInTheDocument();
  });

  it('无权限或非法 tab 回退到第一个可见模块', () => {
    render(
      <MemoryRouter initialEntries={['/admin?tab=monitoring']}>
        <AdminPanel />
      </MemoryRouter>,
    );

    expect(screen.getByText('企业管理内容')).toBeInTheDocument();
    expect(screen.queryByText('快麦接入内容')).not.toBeInTheDocument();
  });
});
