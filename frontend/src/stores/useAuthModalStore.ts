/**
 * 认证弹窗状态管理
 *
 * 管理登录/注册弹窗的打开/关闭状态和当前显示的表单类型
 */

import { create } from 'zustand';

type AuthModalMode = 'login' | 'register';

interface AuthModalState {
  isOpen: boolean;
  mode: AuthModalMode;

  openLogin: () => void;
  openRegister: () => void;
  close: () => void;
  switchMode: () => void;
}

export const useAuthModalStore = create<AuthModalState>((set) => ({
  isOpen: false,
  mode: 'login',

  openLogin: () => set({ isOpen: true, mode: 'login' }),
  openRegister: () => set({ isOpen: true, mode: 'register' }),
  close: () => set({ isOpen: false }),
  switchMode: () => set((state) => ({ mode: state.mode === 'login' ? 'register' : 'login' })),
}));
