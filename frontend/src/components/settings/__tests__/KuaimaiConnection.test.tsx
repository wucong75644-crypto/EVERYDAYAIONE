import { render, screen, cleanup } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import KuaimaiConnection from '../KuaimaiConnection';
import * as api from '../../../services/kuaimaiExternal';
vi.mock('../../../services/kuaimaiExternal', () => ({ listCredentials: vi.fn(), startKuaimaiLogin: vi.fn(), loginKuaimai: vi.fn(), testCredential: vi.fn(), deleteCredential: vi.fn() }));
beforeEach(() => {
  vi.clearAllMocks();
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
