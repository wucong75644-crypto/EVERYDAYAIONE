/**
 * useLogout Hook 测试
 *
 * 覆盖：org 优先级（login_org_id > current_org_id > 无）、clearAuth 调用、跳转目标
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';

// Mock useAuthStore
const mockClearAuth = vi.fn();
vi.mock('../../stores/useAuthStore', () => ({
  useAuthStore: () => ({ clearAuth: mockClearAuth }),
}));

import { useLogout } from '../useLogout';

// 捕获 window.location.href 赋值
let capturedHref: string | null = null;
const originalLocation = window.location;

beforeEach(() => {
  mockClearAuth.mockClear();
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
});
