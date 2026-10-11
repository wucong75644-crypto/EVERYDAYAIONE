import { afterEach, expect, it, vi } from 'vitest';
import api from '../api';
import { startReachLogin, importReachSession } from '../agentReach';
import { loginKuaimai, startKuaimaiLogin } from '../kuaimaiExternal';

afterEach(() => vi.restoreAllMocks());
it('shows the sanitized login service reason instead of a generic HTTP status', async () => {
  vi.spyOn(api, 'request').mockRejectedValue({ isAxiosError: true, message: 'Request failed with status code 422', response: { status: 422, data: { detail: 'B站返回的登录地址无法验证' } } });
  await expect(startReachLogin('bilibili')).rejects.toThrow('B站返回的登录地址无法验证');
});
it('does not echo credentials in validation errors', async () => {
  vi.spyOn(api, 'request').mockRejectedValue({ isAxiosError: true, response: { status: 422, data: { detail: [{ input: 'private-cookie', msg: 'bad value' }] } } });
  await expect(importReachSession('twitter', 'private-cookie')).rejects.toThrow('连接请求格式不正确，请刷新页面后重试。');
});
it('preserves existing structured errors', async () => {
  vi.spyOn(api, 'request').mockRejectedValue({ isAxiosError: true, response: { status: 403, data: { error: { code: 'ACCESS_DENIED', message: '无权连接组织账号' } } } });
  await expect(startReachLogin('youtube')).rejects.toMatchObject({ code: 'ACCESS_DENIED', message: '无权连接组织账号' });
});

it('shows the sanitized ERP login reason', async () => {
  vi.spyOn(api, 'request').mockRejectedValue({ isAxiosError: true, response: { status: 400, data: { detail: '登录会话已过期或已使用，请重新开始。' } } });
  await expect(startKuaimaiLogin()).rejects.toThrow('登录会话已过期或已使用，请重新开始。');
});
it('does not echo an ERP password in validation errors', async () => {
  vi.spyOn(api, 'request').mockRejectedValue({ isAxiosError: true, response: { status: 422, data: { detail: [{ input: 'private-password', msg: 'bad value' }] } } });
  await expect(loginKuaimai({ session_id: 'session', company_name: 'company', username: 'user', password: 'private-password', validation_code: '' })).rejects.toThrow('连接请求格式不正确，请刷新页面后重试。');
});

it('waits for the bounded server login check instead of abandoning it after 30 seconds', async () => {
  const { pollReachLogin } = await import('../agentReach');
  const request = vi.spyOn(api, 'request').mockResolvedValue({ data: { state: 'waiting' } });
  await pollReachLogin('session');
  expect(request).toHaveBeenCalledWith(expect.objectContaining({ timeout: 95000 }));
});
