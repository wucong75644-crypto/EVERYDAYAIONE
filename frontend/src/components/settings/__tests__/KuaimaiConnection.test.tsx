import { render, screen, cleanup } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import KuaimaiConnection from '../KuaimaiConnection';
import * as api from '../../../services/kuaimaiExternal';
vi.mock('../../../services/kuaimaiExternal', () => ({ startKuaimaiWechat: vi.fn(), pollKuaimaiWechat: vi.fn(), cancelKuaimaiWechat: vi.fn(), listCredentials: vi.fn(), startKuaimaiLogin: vi.fn(), loginKuaimai: vi.fn(), testCredential: vi.fn(), deleteCredential: vi.fn() }));
beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.startKuaimaiWechat).mockResolvedValue({ id: 'qr-session', state: 'waiting', expires_at: Date.now()/1000+240, image: 'data:image/png;base64,cG5n', message: '' });
  vi.mocked(api.cancelKuaimaiWechat).mockResolvedValue({ id: 'qr-session', state: 'cancelled', expires_at: Date.now()/1000+240, message: '' });
  vi.mocked(api.listCredentials).mockResolvedValue([]);
  vi.mocked(api.startKuaimaiLogin).mockResolvedValue({ id:'session', expires_at:Date.now()/1000+600 });
});
afterEach(cleanup);
async function fill() {
  const user = userEvent.setup();
  await user.click(screen.getByRole('button', {name:'连接快麦账号'}));
  await user.type(await screen.findByLabelText('快麦公司名称'), '公司');
  await user.type(screen.getByLabelText('快麦账号'), '账号');
  await user.type(screen.getByLabelText('快麦密码'), 'test-secret');
  await user.click(screen.getByRole('button', {name:'登录并连接'}));
}
it('connects both sources and removes password on success', async () => {
  vi.mocked(api.loginKuaimai).mockResolvedValue({state:'connected', sources:['thinktank','viperp'], message:'已连接两个数据源'});
  render(<KuaimaiConnection admin />); await fill();
  await screen.findByText('已连接两个数据源');
  expect(api.loginKuaimai).toHaveBeenCalledWith({session_id:'session', company_name:'公司', username:'账号', password:'test-secret', validation_code:''});
  expect(screen.queryByLabelText('快麦密码')).not.toBeInTheDocument();
});
it('shows fresh captcha and clears submitted password', async () => {
  vi.mocked(api.loginKuaimai).mockResolvedValue({state:'captcha', id:'next', expires_at:Date.now()/1000+600, captcha_image:'data:image/png;base64,cG5n', message:'需要验证码'});
  render(<KuaimaiConnection admin />); await fill();
  await screen.findByLabelText('图片验证码');
  expect(screen.getByLabelText('快麦密码')).toHaveValue('');
  expect(screen.getByAltText('快麦图片验证码')).toBeInTheDocument();
});
it('hides administrator login from members', () => {
  render(<KuaimaiConnection admin={false} />);
  expect(api.listCredentials).not.toHaveBeenCalled();
  expect(screen.queryByRole('button')).not.toBeInTheDocument();
});
it('discards consumed session on failure', async () => {
  vi.mocked(api.loginKuaimai).mockRejectedValue(new Error('登录失败'));
  render(<KuaimaiConnection admin />); await fill();
  await screen.findByRole('alert');
  expect(screen.queryByLabelText('快麦密码')).not.toBeInTheDocument();
  expect(screen.getByRole('button', {name:'连接快麦账号'})).toBeInTheDocument();
});

it('shows a WeChat QR without asking for a password and cancels it', async () => {
  const user = userEvent.setup(); render(<KuaimaiConnection admin />);
  await user.click(screen.getByRole('button', {name:'微信扫码登录'}));
  await screen.findByAltText('快麦微信登录二维码');
  expect(screen.queryByLabelText('快麦密码')).not.toBeInTheDocument();
  await user.click(screen.getByRole('button', {name:'取消扫码'}));
  expect(api.cancelKuaimaiWechat).toHaveBeenCalledWith('qr-session');
  await screen.findByRole('button', {name:'微信扫码登录'});
});
it('automatically refreshes the account rows after confirmed WeChat login', async () => {
  vi.mocked(api.pollKuaimaiWechat).mockResolvedValue({id:'qr-session', state:'connected', expires_at:Date.now()/1000+240, message:'微信登录已连接'});
  const onChange = vi.fn(); const user = userEvent.setup();
  render(<KuaimaiConnection admin onCredentialsChange={onChange} />);
  await user.click(screen.getByRole('button', {name:'微信扫码登录'}));
  await screen.findByText('微信登录已连接', {}, {timeout:4000});
  expect(api.listCredentials).toHaveBeenCalledTimes(2);
  expect(screen.queryByAltText('快麦微信登录二维码')).not.toBeInTheDocument();
  expect(onChange).toHaveBeenCalledTimes(2);
});
it('shows an unbound-account reason and allows a fresh QR', async () => {
  vi.mocked(api.pollKuaimaiWechat).mockResolvedValue({id:'qr-session', state:'failed', expires_at:Date.now()/1000+240, message:'此微信尚未绑定快麦账号，请先在快麦官网完成绑定。'});
  const user = userEvent.setup(); render(<KuaimaiConnection admin />);
  await user.click(screen.getByRole('button', {name:'微信扫码登录'}));
  await screen.findByText('此微信尚未绑定快麦账号，请先在快麦官网完成绑定。', {}, {timeout:4000});
  expect(screen.queryByAltText('快麦微信登录二维码')).not.toBeInTheDocument();
  await user.click(screen.getByRole('button', {name:'刷新微信二维码'}));
  expect(api.startKuaimaiWechat).toHaveBeenCalledTimes(2);
});
