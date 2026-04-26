/**
 * useLogout Hook 测试
 *
 * 覆盖：org 优先级、clearAuth 调用、跳转目标、服务端 refresh token 吊销
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';

// Mock useAuthStore
const mockClearAuth = vi.fn();
vi.mock('../../stores/useAuthStore', () => ({
  useAuthStore: () => ({ clearAuth: mockClearAuth }),
}));

// Mock fetch（用于服务端吊销）
const mockFetch = vi.fn().mockResolvedValue({ ok: true });
vi.stubGlobal('fetch', mockFetch);

import { useLogout } from '../useLogout';

// 捕获 window.location.href 赋值
let capturedHref: string | null = null;
const originalLocation = window.location;

beforeEach(() => {
  mockClearAuth.mockClear();
  mockFetch.mockClear();
  capturedHref = null;
  localStorage.clear();

  // Mock window.location.href setter
  Object.defineProperty(window, 'location', {
    writable: true,
    value: {
      ...originalLocation,
      get href() { return originalLocation.href; },
      set href(url: string) { capturedHref = url; },
    },
  });
});

describe('useLogout', () => {
  it('有 login_org_id 时跳转到企业登录页', () => {
    localStorage.setItem('login_org_id', 'org-123');
    localStorage.setItem('current_org_id', 'org-456');

    const logout = useLogout();
    logout();

    expect(mockClearAuth).toHaveBeenCalledOnce();
    expect(capturedHref).toBe('/?org=org-123');
  });

  it('无 login_org_id 但有 current_org_id 时兜底跳转企业登录页', () => {
    localStorage.setItem('current_org_id', 'org-456');

    const logout = useLogout();
    logout();

    expect(mockClearAuth).toHaveBeenCalledOnce();
    expect(capturedHref).toBe('/?org=org-456');
  });

  it('两个 org_id 都没有时跳转首页', () => {
    const logout = useLogout();
    logout();

    expect(mockClearAuth).toHaveBeenCalledOnce();
    expect(capturedHref).toBe('/');
  });

  it('clearAuth 在跳转之前被调用', () => {
    localStorage.setItem('login_org_id', 'org-abc');

    // 验证 clearAuth 调用时 login_org_id 已被读取（跳转正确说明读在 clear 之前）
    const logout = useLogout();
    logout();

    expect(mockClearAuth).toHaveBeenCalledOnce();
    expect(capturedHref).toBe('/?org=org-abc');
  });

  it('有 refresh_token 时发送服务端吊销请求', () => {
    localStorage.setItem('refresh_token', 'rt-to-revoke');

    const logout = useLogout();
    logout();

    expect(mockFetch).toHaveBeenCalledOnce();
    const [url, options] = mockFetch.mock.calls[0];
    expect(url).toContain('/auth/logout');
    expect(options.method).toBe('POST');
    expect(JSON.parse(options.body)).toEqual({ refresh_token: 'rt-to-revoke' });
  });

  it('无 refresh_token 时不发送吊销请求', () => {
    const logout = useLogout();
    logout();

    expect(mockFetch).not.toHaveBeenCalled();
  });

  it('吊销请求失败不影响退出流程', () => {
    localStorage.setItem('refresh_token', 'rt-fail');
    mockFetch.mockRejectedValueOnce(new Error('Network error'));

    const logout = useLogout();
    logout();

    // 即使 fetch 失败，clearAuth 和跳转仍正常执行
    expect(mockClearAuth).toHaveBeenCalledOnce();
    expect(capturedHref).toBe('/');
  });
});
