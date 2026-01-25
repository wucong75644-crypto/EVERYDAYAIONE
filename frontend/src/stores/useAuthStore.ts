/**
 * 认证状态管理
 */

import { create } from 'zustand';
import type { User } from '../types/auth';
import { getCurrentUser } from '../services/auth';

interface AuthState {
  user: User | null;
  isAuthenticated: boolean;
  isLoading: boolean;

  setUser: (user: User | null) => void;
  setToken: (token: string) => void;
  clearAuth: () => void;
  initAuth: () => void;
  refreshUser: () => Promise<void>;
}

export const useAuthStore = create<AuthState>((set) => ({
  user: null,
  isAuthenticated: false,
  isLoading: true,

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

  clearAuth: () => {
    localStorage.removeItem('access_token');
    localStorage.removeItem('user');
    localStorage.removeItem('everydayai_conversations_cache'); // 清除对话列表缓存
    localStorage.removeItem('everydayai_message_cache'); // 清除消息缓存
    set({ user: null, isAuthenticated: false });
  },

  initAuth: () => {
    const token = localStorage.getItem('access_token');
    const userStr = localStorage.getItem('user');

    if (token && userStr) {
      try {
        const user = JSON.parse(userStr) as User;
        set({ user, isAuthenticated: true, isLoading: false });
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
      console.error('刷新用户信息失败:', error);
    }
  },
}));
