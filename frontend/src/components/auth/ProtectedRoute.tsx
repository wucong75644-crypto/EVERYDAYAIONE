/**
 * 路由守卫组件（Protected Route）
 *
 * 大厂最佳实践：集中式认证检查，避免在各页面组件中重复逻辑
 *
 * 功能：
 * 1. 检查用户认证状态
 * 2. 未登录时自动保存当前 URL 并重定向到登录页
 * 3. 登录后自动跳转回原页面
 * 4. 支持嵌套路由和自定义重定向路径
 *
 * 使用示例：
 * ```tsx
 * <Route path="/chat" element={<ProtectedRoute><Chat /></ProtectedRoute>} />
 * ```
 */

import type { ReactNode } from 'react';
import { Navigate, useLocation } from 'react-router-dom';
import { useAuthStore } from '../../stores/useAuthStore';

interface ProtectedRouteProps {
  children: ReactNode;
  /**
   * 未登录时重定向的路径
   * @default '/login'
   */
  redirectTo?: string;
}

export default function ProtectedRoute({
  children,
  redirectTo = '/login',
}: ProtectedRouteProps) {
  const { isAuthenticated } = useAuthStore();
  const location = useLocation();

  if (!isAuthenticated) {
    // 保存当前路径（包括 query 参数），登录成功后跳转回来
    // 使用 location.state 传递，符合 React Router v6 官方推荐
    return <Navigate to={redirectTo} state={{ from: location.pathname }} replace />;
  }

  // 已登录，正常渲染子组件
  return <>{children}</>;
}
