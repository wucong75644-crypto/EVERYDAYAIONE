import { render, screen, waitFor, cleanup } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import ReachConnections from '../ReachConnections';
import * as reach from '../../../services/agentReach';
const state = vi.hoisted(() => ({ org: { org_id: 'org', name: 'Organization', role: 'owner' } }));
vi.mock('../../../stores/useAuthStore', () => ({ useAuthStore: (selector: (state: unknown) => unknown) => selector({ currentOrg: state.org }) }));
vi.mock('../../../services/org', () => ({ listMembers: vi.fn().mockResolvedValue([{ user_id: 'member', nickname: '成员', status: 'active' }]) }));
vi.mock('../../../services/agentReach', () => ({ listReachConnections: vi.fn(), listReachOperations: vi.fn(), grantReachConnection: vi.fn(), revokeReachConnection: vi.fn(), listReachLoginMethods: vi.fn(), startReachLogin: vi.fn(), pollReachLogin: vi.fn(), cancelReachLogin: vi.fn(), importReachSession: vi.fn(), checkReachConnection: vi.fn() }));
vi.mock('react-hot-toast', () => ({ toast: { success: vi.fn() } }));
const row: reach.ReachConnection = { id: 'connection', platform: 'bilibili', account_id: '123', display_name: '组织账号', status: 'active', login_state: 'connected' };
beforeEach(() => {
  vi.clearAllMocks(); state.org.role = 'owner';
  vi.mocked(reach.listReachConnections).mockResolvedValue([]);
  vi.mocked(reach.listReachOperations).mockResolvedValue([]);
  vi.mocked(reach.listReachLoginMethods).mockResolvedValue([
    { platform: 'twitter', method: 'import', enabled: true, message: '需要导入Cookie，不支持一键授权' },
    { platform: 'bilibili', method: 'qr', enabled: true, message: '' },
    { platform: 'xiaohongshu', method: 'qr', enabled: false, message: '本组织扫码服务待配置' },
    { platform: 'youtube', method: 'oauth', enabled: true, message: '' },
  ]);
  vi.mocked(reach.cancelReachLogin).mockResolvedValue({});
  vi.mocked(reach.importReachSession).mockResolvedValue(row);
});
afterEach(cleanup);
it('imports a session without JSON/account ID and clears credentials', async () => {
  const user = userEvent.setup(); render(<ReachConnections />);
  await screen.findByText('尚未连接账号。');
  expect(screen.queryByLabelText('账号凭据（JSON）')).not.toBeInTheDocument();
  expect(screen.queryByLabelText('平台账号 ID')).not.toBeInTheDocument();
  await user.click(screen.getByRole('button', { name: '连接账号' }));
  await user.type(screen.getByLabelText('登录会话 Cookie'), 'auth_token=dummy; ct0=dummy');
  await user.click(screen.getByRole('button', { name: '验证并连接' }));
  await waitFor(() => expect(reach.importReachSession).toHaveBeenCalledWith('twitter', 'auth_token=dummy; ct0=dummy', undefined));
  expect(screen.getByLabelText('登录会话 Cookie')).toHaveValue('');
});
it('member sees own receipts without admin controls', async () => {
  state.org.role = 'member';
  vi.mocked(reach.listReachOperations).mockResolvedValue([{ id: 'operation', platform: 'twitter', account_id: '123', state: 'uncertain', receipt: { action: 'create_post' }, created_at: '' }]);
  render(<ReachConnections />); await screen.findByText(/结果待核实，请勿重复提交/);
  expect(screen.queryByRole('button', { name: '连接账号' })).not.toBeInTheDocument();
});
it('disables unconfigured platform with explanation', async () => {
  const user = userEvent.setup(); render(<ReachConnections />); await screen.findByText('尚未连接账号。');
  await user.selectOptions(screen.getByLabelText('平台'), 'xiaohongshu');
  expect(screen.getByRole('button', { name: '连接账号' })).toBeDisabled();
  expect(screen.getByText('本组织扫码服务待配置')).toBeInTheDocument();
});
it('shows QR and cancels session on close', async () => {
  vi.mocked(reach.startReachLogin).mockResolvedValue({ id: 'session', platform: 'bilibili', state: 'waiting', expires_at: Date.now() / 1000 + 180, image: 'data:image/png;base64,aGVsbG8=' });
  const user = userEvent.setup(); render(<ReachConnections />); await screen.findByText('尚未连接账号。');
  await user.selectOptions(screen.getByLabelText('平台'), 'bilibili');
  await user.click(screen.getByRole('button', { name: '连接账号' }));
  await user.click(screen.getByRole('button', { name: '生成登录二维码' }));
  expect(await screen.findByAltText('平台登录二维码')).toBeInTheDocument();
  await user.click(screen.getByRole('button', { name: '取消连接' }));
  expect(reach.cancelReachLogin).toHaveBeenCalledWith('session');
});
it('reconnects with existing connection identity', async () => {
  vi.mocked(reach.listReachConnections).mockResolvedValue([row]);
  vi.mocked(reach.startReachLogin).mockResolvedValue({ id: 'session', platform: 'bilibili', state: 'connected', expires_at: Date.now() / 1000 + 180, connection: row });
  const user = userEvent.setup(); render(<ReachConnections />); await screen.findByText('B 站 · 组织账号', { selector: 'p' });
  await user.click(screen.getByRole('button', { name: '重新连接' }));
  await user.click(screen.getByRole('button', { name: '生成登录二维码' }));
  await screen.findByText('账号连接成功');
  expect(reach.startReachLogin).toHaveBeenCalledWith('bilibili', 'connection');
  expect(screen.queryByAltText('平台登录二维码')).not.toBeInTheDocument();
  cleanup(); expect(reach.cancelReachLogin).not.toHaveBeenCalled();
});
it('links to official OAuth with opener protection', async () => {
  vi.mocked(reach.startReachLogin).mockResolvedValue({ id: 'session', platform: 'youtube', state: 'waiting', expires_at: Date.now() / 1000 + 600, authorize_url: 'https://accounts.google.com/o/oauth2/v2/auth?state=test' });
  const user = userEvent.setup(); render(<ReachConnections />); await screen.findByText('尚未连接账号。');
  await user.selectOptions(screen.getByLabelText('平台'), 'youtube');
  await user.click(screen.getByRole('button', { name: '连接账号' }));
  await user.click(screen.getByRole('button', { name: '准备官方授权' }));
  expect(await screen.findByRole('link', { name: '打开 YouTube 官方授权页' })).toHaveAttribute('rel', 'noopener noreferrer');
});
