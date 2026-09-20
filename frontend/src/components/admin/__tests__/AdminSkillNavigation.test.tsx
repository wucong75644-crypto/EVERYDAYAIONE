import { fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { MemoryRouter } from 'react-router-dom';
import Admin from '../../../pages/Admin';
import type { SkillNavigationState } from '../skills/presentation';

const { navigate } = vi.hoisted(() => ({ navigate: vi.fn() }));
vi.mock('react-router-dom', async importOriginal => ({ ...await importOriginal<typeof import('react-router-dom')>(), useNavigate: () => navigate }));
vi.mock('../../../stores/useAuthStore', () => ({ useAuthStore: (selector?: (s: unknown) => unknown) => {
  const state = { user: { role: 'super_admin' }, currentOrg: { org_id: 'org1', role: 'owner' }, isLoading: false };
  return selector ? selector(state) : state;
} }));
vi.mock('../SuperAdminPanel', () => ({ default: () => <div>平台页面</div> }));
vi.mock('../OrgManagePanel', () => ({ default: () => <div>组织页面</div> }));
vi.mock('../../integrations/KuaimaiIntegrationPanel', () => ({ default: () => null }));
vi.mock('../SkillAdminPanel', () => ({ default: ({ onNavigationStateChange }: { onNavigationStateChange: (s: SkillNavigationState) => void }) => <div>编辑工作区<button onClick={() => onNavigationStateChange({ dirty: true, busy: false })}>模拟编辑</button><button onClick={() => onNavigationStateChange({ dirty: true, busy: true })}>模拟保存</button></div> }));

beforeEach(() => { vi.restoreAllMocks(); });
describe('Skill navigation protection', () => {
  it('confirms abandoning unsaved edits before switching admin sections', async () => {
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false);
    render(<MemoryRouter><Admin /></MemoryRouter>);
    fireEvent.click(screen.getByRole('button', { name: 'Skill 管理' }));
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
    fireEvent.click(screen.getByRole('button', { name: 'Skill 管理' }));
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
