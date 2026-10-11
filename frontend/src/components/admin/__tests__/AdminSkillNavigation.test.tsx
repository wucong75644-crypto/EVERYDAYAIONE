import { fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { MemoryRouter } from 'react-router-dom';
import Admin from '../../../pages/Admin';
import type { SkillNavigationState } from '../skills/presentation';

const { navigate, auth } = vi.hoisted(() => ({ navigate: vi.fn(), auth: { user: { role: 'super_admin' }, currentOrg: { org_id: 'org1', role: 'owner' }, isLoading: false } }));
vi.mock('react-router-dom', async importOriginal => ({ ...await importOriginal<typeof import('react-router-dom')>(), useNavigate: () => navigate }));
vi.mock('../../../stores/useAuthStore', () => ({ useAuthStore: (selector?: (s: unknown) => unknown) => {
  const state = auth;
  return selector ? selector(state) : state;
} }));
vi.mock('../../settings/ReachConnections', () => ({ default: () => <div>独立账号连接页面</div> }));
vi.mock('../SuperAdminPanel', () => ({ default: () => <div>平台页面</div> }));
vi.mock('../OrgManagePanel', () => ({ default: () => <div>组织页面</div> }));
vi.mock('../../integrations/KuaimaiIntegrationPanel', () => ({ default: () => null }));
vi.mock('../SkillAdminPanel', () => ({ default: ({ onNavigationStateChange }: { onNavigationStateChange: (s: SkillNavigationState) => void }) => <div>编辑工作区<button onClick={() => onNavigationStateChange({ dirty: true, busy: false })}>模拟编辑</button><button onClick={() => onNavigationStateChange({ dirty: true, busy: true })}>模拟保存</button></div> }));

beforeEach(() => { vi.restoreAllMocks(); window.history.replaceState(null, '', '/admin'); auth.user.role = 'super_admin'; auth.currentOrg.role = 'owner'; });
describe('Skill navigation protection', () => {
  it('confirms abandoning unsaved edits before switching admin sections', async () => {
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false);
    render(<MemoryRouter><Admin /></MemoryRouter>);
    fireEvent.click(screen.getByRole('button', { name: '我的 Skill' }));
    fireEvent.click(await screen.findByRole('button', { name: '模拟编辑' }));
    fireEvent.click(screen.getByRole('button', { name: '企业管理' }));
    expect(confirm).toHaveBeenCalled();
    expect(screen.getByText('编辑工作区')).toBeInTheDocument();
    confirm.mockReturnValue(true);
    fireEvent.click(screen.getByRole('button', { name: '企业管理' }));
    expect(screen.getByText('组织页面')).toBeInTheDocument();
  });
  it('protects the page back button and prevents leaving during a write', async () => {
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false);
    render(<MemoryRouter><Admin /></MemoryRouter>);
    fireEvent.click(screen.getByRole('button', { name: '我的 Skill' }));
    fireEvent.click(await screen.findByRole('button', { name: '模拟编辑' }));
    fireEvent.click(screen.getByRole('button', { name: '返回', exact: true }));
    expect(navigate).not.toHaveBeenCalled();
    confirm.mockReturnValue(true);
    fireEvent.click(screen.getByRole('button', { name: '返回', exact: true }));
    expect(navigate).toHaveBeenCalledWith(-1);
    fireEvent.click(screen.getByRole('button', { name: '模拟保存' }));
    expect(screen.getByRole('button', { name: '返回', exact: true })).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: '企业管理' }));
    expect(screen.getByText('编辑工作区')).toBeInTheDocument();
  });
});

describe('Account administration navigation', () => {
  it('opens the account deep link as a full backend section', () => {
    window.history.replaceState(null, '', '/admin?tab=accounts');
    render(<MemoryRouter><Admin /></MemoryRouter>);
    expect(screen.getByText('独立账号连接页面')).toBeInTheDocument();
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '企业管理' }));
    expect(window.location.search).toBe('?tab=org');
    fireEvent.click(screen.getByRole('button', { name: '账号连接' }));
    expect(screen.getByText('独立账号连接页面')).toBeInTheDocument();
  });
  it('does not grant organization account administration through a deep link', async () => {
    auth.user.role = 'user'; auth.currentOrg.role = 'member';
    window.history.replaceState(null, '', '/admin?tab=accounts');
    render(<MemoryRouter><Admin /></MemoryRouter>);
    expect(screen.queryByText('独立账号连接页面')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '账号连接' })).not.toBeInTheDocument();
    expect(await screen.findByText('编辑工作区')).toBeInTheDocument();
  });
});
