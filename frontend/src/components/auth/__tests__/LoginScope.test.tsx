import { beforeEach, afterEach, expect, it, vi } from 'vitest';
import { cleanup, render, screen, fireEvent, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
const { login, listOrgs } = vi.hoisted(() => ({ login: vi.fn(), listOrgs: vi.fn() }));
vi.mock('../../../services/auth', () => ({ loginByPassword: login, loginByPhone: login, loginByOrg: vi.fn(), sendCode: vi.fn(), getOrgNamePublic: vi.fn(), getCurrentUser: vi.fn(), listMyOrganizations: listOrgs }));
vi.mock('../WecomQrLogin', () => ({ default: () => null }));
import LoginForm from '../LoginForm';
import WecomCallback from '../../../pages/WecomCallback';
import { useAuthStore } from '../../../stores/useAuthStore';
import { completeLogin } from '../../../utils/loginOrganization';
import type { User, Organization } from '../../../types/auth';
const user = { id: 'a' } as User;
const org: Organization = { org_id: 'enterprise-a', name: 'Enterprise A', role: 'member' };
const token = { access_token: 'access', refresh_token: 'refresh' };
beforeEach(() => {
  localStorage.clear(); vi.clearAllMocks();
  useAuthStore.setState({ user: null, isAuthenticated: false, currentOrg: null, currentOrgId: null });
  completeLogin(user, org); useAuthStore.getState().clearAuth();
  login.mockResolvedValue({ user, token }); listOrgs.mockResolvedValue([org]);
});
afterEach(cleanup);
function submit(code = false) {
  if (code) fireEvent.click(screen.getByText('验证码登录'));
  fireEvent.change(screen.getByPlaceholderText('请输入手机号'), { target: { value: '13800000000' } });
  fireEvent.change(screen.getByPlaceholderText(code ? '请输入验证码' : '请输入密码'), { target: { value: code ? '123456' : 'password' } });
  fireEvent.click(screen.getByRole('button', { name: '登录', exact: true }));
}
it.each([false, true])('password/code login restores enterprise before success (code=%s)', async code => {
  const success = vi.fn(); render(<LoginForm onSuccess={success} />); submit(code);
  await waitFor(() => expect(success).toHaveBeenCalledOnce());
  expect(useAuthStore.getState().currentOrgId).toBe(org.org_id);
});
it('organization API failure never completes login in personal scope', async () => {
  listOrgs.mockRejectedValue(new Error('offline')); const success = vi.fn();
  render(<LoginForm onSuccess={success} />); submit();
  await screen.findByText(/企业信息暂时无法加载/);
  expect(success).not.toHaveBeenCalled(); expect(useAuthStore.getState().isAuthenticated).toBe(false);
});
it('removed membership requires an explicit personal choice', async () => {
  listOrgs.mockResolvedValue([]); const success = vi.fn(); render(<LoginForm onSuccess={success} />); submit();
  const choose = await screen.findByText('以个人身份继续');
  expect(success).not.toHaveBeenCalled(); expect(useAuthStore.getState().isAuthenticated).toBe(false);
  fireEvent.click(choose); expect(success).toHaveBeenCalledOnce(); expect(useAuthStore.getState().currentOrgId).toBeNull();
});
it('OAuth callback restores the same account enterprise through the shared resolver', async () => {
  const params = new URLSearchParams({ token: btoa(JSON.stringify(token)), user: btoa(JSON.stringify(user)) });
  render(<MemoryRouter initialEntries={[`/callback?${params}`]}><WecomCallback /></MemoryRouter>);
  await waitFor(() => expect(useAuthStore.getState().isAuthenticated).toBe(true));
  expect(useAuthStore.getState().currentOrgId).toBe(org.org_id);
});
