/**
 * tokenManager 单元测试
 *
 * 覆盖：
 * - silentRefresh：成功刷新、失败登出、并发排队
 * - logoutOnce：清除状态 + 跳转、防重复
 * - getAccessToken / getRefreshToken / setTokens / clearTokens
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

// Mock useAuthStore
const mockClearAuth = vi.fn();
const mockSetToken = vi.fn();
vi.mock('../../stores/useAuthStore', () => ({
  useAuthStore: {
    getState: () => ({
      clearAuth: mockClearAuth,
      setToken: mockSetToken,
    }),
  },
}));

// Mock logger
vi.mock('../logger', () => ({
  logger: {
    info: vi.fn(),
    warn: vi.fn(),
    error: vi.fn(),
  },
}));

// Mock axios — vi.mock factory 是提升执行的，不能引用外部 let
// 所以用 vi.hoisted 提升 mock 函数声明
const { mockAxiosPost } = vi.hoisted(() => ({
  mockAxiosPost: vi.fn(),
}));

vi.mock('axios', () => ({
  default: {
    create: () => ({
      request: vi.fn(),
      interceptors: {
        request: { use: vi.fn() },
        response: { use: vi.fn() },
      },
    }),
    post: (...args: unknown[]) => mockAxiosPost(...args),
  },
  AxiosError: class AxiosError extends Error {},
}));

import {
  silentRefresh,
  logoutOnce,
  getAccessToken,
  getRefreshToken,
  setTokens,
  clearTokens,
} from '../tokenManager';

// 捕获 window.location.href
let capturedHref: string | null = null;
const originalLocation = window.location;

beforeEach(() => {
  vi.useFakeTimers();
  vi.clearAllMocks();
  localStorage.clear();
  capturedHref = null;

  Object.defineProperty(window, 'location', {
    writable: true,
    value: {
      ...originalLocation,
      pathname: '/chat',
      get href() { return originalLocation.href; },
      set href(url: string) { capturedHref = url; },
    },
  });
});

afterEach(() => {
  // 推进 300ms 让 logoutOnce 的防抖锁释放
  vi.advanceTimersByTime(500);
  vi.useRealTimers();
  Object.defineProperty(window, 'location', {
    writable: true,
    value: originalLocation,
  });
});

// ── Token 存取工具 ──────────────────────────────────────

describe('Token 存取工具', () => {
  it('getAccessToken 返回 localStorage 值', () => {
    localStorage.setItem('access_token', 'at-123');
    expect(getAccessToken()).toBe('at-123');
  });

  it('getRefreshToken 返回 localStorage 值', () => {
    localStorage.setItem('refresh_token', 'rt-456');
    expect(getRefreshToken()).toBe('rt-456');
  });

  it('getAccessToken 无值时返回 null', () => {
    expect(getAccessToken()).toBeNull();
  });

  it('setTokens 同时设置 access 和 refresh', () => {
    setTokens('at-new', 'rt-new');
    expect(localStorage.getItem('access_token')).toBe('at-new');
    expect(localStorage.getItem('refresh_token')).toBe('rt-new');
  });

  it('clearTokens 清除两个 token', () => {
    setTokens('at', 'rt');
    clearTokens();
    expect(localStorage.getItem('access_token')).toBeNull();
    expect(localStorage.getItem('refresh_token')).toBeNull();
  });
});

// ── silentRefresh ───────────────────────────────────────

describe('silentRefresh', () => {
  it('刷新成功 → 更新 localStorage + 返回新 access_token', async () => {
    localStorage.setItem('refresh_token', 'old-rt');

    mockAxiosPost.mockResolvedValueOnce({
      data: {
        token: {
          access_token: 'new-at',
          refresh_token: 'new-rt',
        },
      },
    });

    const result = await silentRefresh();

    expect(result).toBe('new-at');
    expect(localStorage.getItem('access_token')).toBe('new-at');
    expect(localStorage.getItem('refresh_token')).toBe('new-rt');
    expect(mockSetToken).toHaveBeenCalledWith('new-at');
  });

  it('无 refresh_token → 调 logoutOnce + 抛错', async () => {
    // 没有 refresh_token
    await expect(silentRefresh()).rejects.toThrow('No refresh token');
    expect(mockClearAuth).toHaveBeenCalled();
  });

  it('刷新失败 → 调 logoutOnce + 抛错', async () => {
    localStorage.setItem('refresh_token', 'some-rt');
    mockAxiosPost.mockRejectedValueOnce(new Error('Network error'));

    await expect(silentRefresh()).rejects.toThrow('Network error');
    expect(mockClearAuth).toHaveBeenCalled();
  });
});

// ── logoutOnce ──────────────────────────────────────────

describe('logoutOnce', () => {
  it('清除 Zustand 状态 + 跳转首页', () => {
    logoutOnce();
    expect(mockClearAuth).toHaveBeenCalledOnce();
    expect(capturedHref).toBe('/');
  });

  it('企业用户跳转带 org 参数', () => {
    localStorage.setItem('login_org_id', 'org-123');
    logoutOnce();
    expect(capturedHref).toBe('/?org=org-123');
  });

  it('已在首页时不跳转', () => {
    Object.defineProperty(window, 'location', {
      writable: true,
      value: {
        ...originalLocation,
        pathname: '/',
        get href() { return originalLocation.href; },
        set href(url: string) { capturedHref = url; },
      },
    });

    logoutOnce();
    expect(mockClearAuth).toHaveBeenCalled();
    expect(capturedHref).toBeNull();
  });
});
