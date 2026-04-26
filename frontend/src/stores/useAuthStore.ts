/**
 * 认证状态管理
 */

import { create } from 'zustand';
import type { Organization, User } from '../types/auth';
import { getCurrentUser, listMyOrganizations } from '../services/auth';
import { useSubscriptionStore } from './useSubscriptionStore';
import { useMemoryStore } from './useMemoryStore';
import { useMessageStore } from './useMessageStore';
import { logger } from '../utils/logger';

interface AuthState {
  user: User | null;
  isAuthenticated: boolean;
  isLoading: boolean;

  // 企业上下文
  currentOrgId: string | null;
  currentOrg: Organization | null;
  organizations: Organization[];

  setUser: (user: User | null) => void;
  setToken: (token: string) => void;
  setTokens: (accessToken: string, refreshToken: string) => void;
  setCurrentOrg: (org: Organization | null) => void;
  clearAuth: () => void;
  initAuth: () => void;
  refreshUser: () => Promise<void>;
  fetchOrganizations: () => Promise<void>;
}

export const useAuthStore = create<AuthState>((set) => ({
  user: null,
  isAuthenticated: false,
  isLoading: true,
  currentOrgId: null,
  currentOrg: null,
  organizations: [],

  setUser: (user) => {
    if (user) {
      localStorage.setItem('user', JSON.stringify(user));
    } else {
      localStorage.removeItem('user');
    }
    set({ user, isAuthenticated: !!user });
  },

  setToken: (token) => {
    localStorage.setItem('access_token', token);
  },

  setTokens: (accessToken, refreshToken) => {
    localStorage.setItem('access_token', accessToken);
    localStorage.setItem('refresh_token', refreshToken);
  },

  setCurrentOrg: (org) => {
    if (org) {
      localStorage.setItem('current_org_id', org.org_id);
      localStorage.setItem('current_org', JSON.stringify(org));
    } else {
      localStorage.removeItem('current_org_id');
      localStorage.removeItem('current_org');
    }
    // 切换企业后清除缓存（不同企业数据隔离）
    localStorage.removeItem('everydayai_conversations_cache');
    localStorage.removeItem('everydayai_message_cache');
    useSubscriptionStore.getState().clearSubscriptions();
    useMemoryStore.getState().reset();
    useMessageStore.getState().reset();
    set({ currentOrgId: org?.org_id ?? null, currentOrg: org });
  },

  clearAuth: () => {
    localStorage.removeItem('access_token');
    localStorage.removeItem('refresh_token');
    localStorage.removeItem('user');
    localStorage.removeItem('current_org_id');
    localStorage.removeItem('current_org');
    localStorage.removeItem('everydayai_conversations_cache');
    localStorage.removeItem('everydayai_message_cache');
    useSubscriptionStore.getState().clearSubscriptions();
    useMemoryStore.getState().reset();
    useMessageStore.getState().reset();
    set({
      user: null, isAuthenticated: false,
      currentOrgId: null, currentOrg: null, organizations: [],
    });
  },

  initAuth: () => {
    const token = localStorage.getItem('access_token');
    const userStr = localStorage.getItem('user');
    const orgId = localStorage.getItem('current_org_id');
    const orgStr = localStorage.getItem('current_org');

    let currentOrg: Organization | null = null;
    if (orgStr) {
      try { currentOrg = JSON.parse(orgStr) as Organization; } catch { /* ignore */ }
    }

    if (token && userStr) {
      try {
        const user = JSON.parse(userStr) as User;
        set({
          user, isAuthenticated: true, isLoading: false,
          currentOrgId: orgId, currentOrg,
        });
      } catch {
        set({ user: null, isAuthenticated: false, isLoading: false });
      }
    } else {
      set({ user: null, isAuthenticated: false, isLoading: false });
    }
  },

  refreshUser: async () => {
    const token = localStorage.getItem('access_token');
    if (!token) return;

    try {
      const user = await getCurrentUser();
      localStorage.setItem('user', JSON.stringify(user));
      set({ user, isAuthenticated: true });
    } catch (error) {
      logger.error('auth', '刷新用户信息失败', error);
    }
  },

  fetchOrganizations: async () => {
    try {
      const orgs = await listMyOrganizations();
      set({ organizations: orgs });
    } catch (error) {
      logger.error('auth', '获取企业列表失败', error);
    }
  },
}));
