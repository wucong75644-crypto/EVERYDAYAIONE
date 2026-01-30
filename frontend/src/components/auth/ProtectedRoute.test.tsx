/**
 * ProtectedRoute 单元测试
 *
 * 测试场景：
 * 1. 未登录用户访问受保护路由 - 应重定向到登录页并保存原始路径
 * 2. 已登录用户访问受保护路由 - 应正常渲染子组件
 * 3. 自定义重定向路径 - 应使用自定义路径而非默认 /login
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter, Routes, Route } from 'react-router-dom';
import ProtectedRoute from './ProtectedRoute';
import { useAuthStore } from '../../stores/useAuthStore';

// Mock useAuthStore
vi.mock('../../stores/useAuthStore', () => ({
  useAuthStore: vi.fn(),
}));

describe('ProtectedRoute', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('未登录用户应重定向到登录页并保存原始路径', () => {
    // 模拟未登录状态
    vi.mocked(useAuthStore).mockReturnValue({
      isAuthenticated: false,
      user: null,
      isLoading: false,
      setUser: vi.fn(),
      setToken: vi.fn(),
      clearAuth: vi.fn(),
      initAuth: vi.fn(),
      refreshUser: vi.fn(),
    });

    // 访问受保护的 /chat 路由
    render(
      <MemoryRouter initialEntries={['/chat']}>
        <Routes>
          <Route
            path="/chat"
            element={
              <ProtectedRoute>
                <div>Chat Page</div>
              </ProtectedRoute>
            }
          />
          <Route
            path="/login"
            element={
              <div>Login Page</div>
            }
          />
        </Routes>
      </MemoryRouter>
    );

    // 应该显示登录页而不是聊天页
    expect(screen.getByText('Login Page')).toBeInTheDocument();
    expect(screen.queryByText('Chat Page')).not.toBeInTheDocument();
  });

  it('已登录用户应正常渲染子组件', () => {
    // 模拟已登录状态
    vi.mocked(useAuthStore).mockReturnValue({
      isAuthenticated: true,
      user: { id: '1', nickname: 'Test User', phone: '13800138000', credits: 100 },
      isLoading: false,
      setUser: vi.fn(),
      setToken: vi.fn(),
      clearAuth: vi.fn(),
      initAuth: vi.fn(),
      refreshUser: vi.fn(),
    });

    // 访问受保护的 /chat 路由
    render(
      <MemoryRouter initialEntries={['/chat']}>
        <Routes>
          <Route
            path="/chat"
            element={
              <ProtectedRoute>
                <div>Chat Page</div>
              </ProtectedRoute>
            }
          />
          <Route
            path="/login"
            element={
              <div>Login Page</div>
            }
          />
        </Routes>
      </MemoryRouter>
    );

    // 应该显示聊天页
    expect(screen.getByText('Chat Page')).toBeInTheDocument();
    expect(screen.queryByText('Login Page')).not.toBeInTheDocument();
  });

  it('应支持自定义重定向路径', () => {
    // 模拟未登录状态
    vi.mocked(useAuthStore).mockReturnValue({
      isAuthenticated: false,
      user: null,
      isLoading: false,
      setUser: vi.fn(),
      setToken: vi.fn(),
      clearAuth: vi.fn(),
      initAuth: vi.fn(),
      refreshUser: vi.fn(),
    });

    // 使用自定义重定向路径
    render(
      <MemoryRouter initialEntries={['/admin']}>
        <Routes>
          <Route
            path="/admin"
            element={
              <ProtectedRoute redirectTo="/custom-login">
                <div>Admin Page</div>
              </ProtectedRoute>
            }
          />
          <Route
            path="/custom-login"
            element={
              <div>Custom Login Page</div>
            }
          />
          <Route
            path="/login"
            element={
              <div>Default Login Page</div>
            }
          />
        </Routes>
      </MemoryRouter>
    );

    // 应该重定向到自定义登录页
    expect(screen.getByText('Custom Login Page')).toBeInTheDocument();
    expect(screen.queryByText('Admin Page')).not.toBeInTheDocument();
    expect(screen.queryByText('Default Login Page')).not.toBeInTheDocument();
  });

  it('应该在重定向时保留原始路径到 location.state', () => {
    // 模拟未登录状态
    vi.mocked(useAuthStore).mockReturnValue({
      isAuthenticated: false,
      user: null,
      isLoading: false,
      setUser: vi.fn(),
      setToken: vi.fn(),
      clearAuth: vi.fn(),
      initAuth: vi.fn(),
      refreshUser: vi.fn(),
    });

    // 使用自定义 Login 组件来检查 location.state
    const LoginWithState = () => {
      const location = window.location;
      return <div data-testid="login-state">{location.pathname}</div>;
    };

    render(
      <MemoryRouter initialEntries={['/chat/123']}>
        <Routes>
          <Route
            path="/chat/:id"
            element={
              <ProtectedRoute>
                <div>Chat Page</div>
              </ProtectedRoute>
            }
          />
          <Route path="/login" element={<LoginWithState />} />
        </Routes>
      </MemoryRouter>
    );

    // 应该重定向到登录页
    expect(screen.getByTestId('login-state')).toBeInTheDocument();
  });
});
