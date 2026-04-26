/**
 * useAuthStore 单元测试
 *
 * 覆盖新增功能：
 * - setTokens：同时设置 access + refresh token
 * - clearAuth：清除 refresh_token + 重置状态
 */

import { describe, it, expect, beforeEach } from 'vitest';
import { useAuthStore } from '../useAuthStore';

beforeEach(() => {
  localStorage.clear();
  // 重置 Zustand 状态
  useAuthStore.setState({
    user: null,
    isAuthenticated: false,
    isLoading: true,
    currentOrgId: null,
    currentOrg: null,
    organizations: [],
  });
});

describe('setTokens', () => {
  it('同时设置 access_token 和 refresh_token', () => {
    useAuthStore.getState().setTokens('at-123', 'rt-456');

    expect(localStorage.getItem('access_token')).toBe('at-123');
    expect(localStorage.getItem('refresh_token')).toBe('rt-456');
  });

  it('覆盖已有的 token', () => {
    localStorage.setItem('access_token', 'old-at');
    localStorage.setItem('refresh_token', 'old-rt');

    useAuthStore.getState().setTokens('new-at', 'new-rt');

    expect(localStorage.getItem('access_token')).toBe('new-at');
    expect(localStorage.getItem('refresh_token')).toBe('new-rt');
  });
});

describe('clearAuth', () => {
  it('清除所有认证数据（含 refresh_token）', () => {
    // 预设数据
    localStorage.setItem('access_token', 'at');
    localStorage.setItem('refresh_token', 'rt');
    localStorage.setItem('user', '{"id":"u1"}');
    localStorage.setItem('current_org_id', 'org-1');
    localStorage.setItem('current_org', '{}');

    useAuthStore.getState().clearAuth();

    expect(localStorage.getItem('access_token')).toBeNull();
    expect(localStorage.getItem('refresh_token')).toBeNull();
    expect(localStorage.getItem('user')).toBeNull();
    expect(localStorage.getItem('current_org_id')).toBeNull();
    expect(localStorage.getItem('current_org')).toBeNull();
  });

  it('重置 Zustand 状态', () => {
    useAuthStore.setState({
      user: { id: 'u1' } as any,
      isAuthenticated: true,
      currentOrgId: 'org-1',
    });

    useAuthStore.getState().clearAuth();

    const state = useAuthStore.getState();
    expect(state.user).toBeNull();
    expect(state.isAuthenticated).toBe(false);
    expect(state.currentOrgId).toBeNull();
  });
});

describe('setToken（单 access_token）', () => {
  it('只设置 access_token，不影响 refresh_token', () => {
    localStorage.setItem('refresh_token', 'rt-existing');

    useAuthStore.getState().setToken('at-new');

    expect(localStorage.getItem('access_token')).toBe('at-new');
    expect(localStorage.getItem('refresh_token')).toBe('rt-existing');
  });
});
