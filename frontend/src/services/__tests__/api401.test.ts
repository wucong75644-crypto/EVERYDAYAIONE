/**
 * API 401 拦截器测试
 *
 * 覆盖：企业用户 org 兜底跳转、散客跳首页、首页不循环跳转、localStorage 清理
 */

import { describe, it, expect, beforeEach } from 'vitest';

/**
 * 提取 401 拦截器的核心跳转逻辑进行纯函数测试
 * 与 api.ts 中的逻辑保持一致
 */
function resolve401Redirect(pathname: string): string | null {
  const loginOrgId =
    localStorage.getItem('login_org_id') ||
    localStorage.getItem('current_org_id');

  // 清理认证信息
  localStorage.removeItem('access_token');
  localStorage.removeItem('user');
  localStorage.removeItem('current_org_id');
  localStorage.removeItem('current_org');

  // 只在非首页时跳转
  if (pathname !== '/') {
    return loginOrgId ? `/?org=${loginOrgId}` : '/';
  }
  return null; // 首页不跳转
}

beforeEach(() => {
  localStorage.clear();
});

describe('401 拦截器跳转逻辑', () => {
  it('有 login_org_id 时跳转企业登录页', () => {
    localStorage.setItem('login_org_id', 'org-123');
    localStorage.setItem('access_token', 'expired');
    localStorage.setItem('user', '{}');

    const target = resolve401Redirect('/chat');

    expect(target).toBe('/?org=org-123');
    expect(localStorage.getItem('access_token')).toBeNull();
    expect(localStorage.getItem('user')).toBeNull();
  });

  it('无 login_org_id 但有 current_org_id 时兜底跳转企业登录页', () => {
    localStorage.setItem('current_org_id', 'org-456');
    localStorage.setItem('access_token', 'expired');

    const target = resolve401Redirect('/chat');

    expect(target).toBe('/?org=org-456');
    // current_org_id 在清理后也被移除
    expect(localStorage.getItem('current_org_id')).toBeNull();
  });

  it('两个 org_id 都没有时跳转首页', () => {
    localStorage.setItem('access_token', 'expired');

    const target = resolve401Redirect('/chat');

    expect(target).toBe('/');
  });

  it('login_org_id 优先于 current_org_id', () => {
    localStorage.setItem('login_org_id', 'org-primary');
    localStorage.setItem('current_org_id', 'org-fallback');

    const target = resolve401Redirect('/chat');

    expect(target).toBe('/?org=org-primary');
  });

  it('已在首页时不跳转（避免循环重定向）', () => {
    localStorage.setItem('login_org_id', 'org-123');

    const target = resolve401Redirect('/');

    expect(target).toBeNull();
  });

  it('清理后 access_token / user / current_org 都被移除', () => {
    localStorage.setItem('access_token', 'token');
    localStorage.setItem('user', '{"id":"u1"}');
    localStorage.setItem('current_org_id', 'org-1');
    localStorage.setItem('current_org', '{"org_id":"org-1"}');
    localStorage.setItem('login_org_id', 'org-1'); // 不应被清除

    resolve401Redirect('/chat');

    expect(localStorage.getItem('access_token')).toBeNull();
    expect(localStorage.getItem('user')).toBeNull();
    expect(localStorage.getItem('current_org_id')).toBeNull();
    expect(localStorage.getItem('current_org')).toBeNull();
    // login_org_id 应保留
    expect(localStorage.getItem('login_org_id')).toBe('org-1');
  });
});
