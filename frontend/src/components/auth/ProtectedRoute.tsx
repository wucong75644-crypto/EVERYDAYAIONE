/**
 * 路由守卫组件（Protected Route）
 *
 * 大厂最佳实践：集中式认证检查，避免在各页面组件中重复逻辑
 *
 * 功能：
 * 1. 检查用户认证状态
 * 2. 未登录时触发登录弹窗（而非重定向到登录页）
 * 3. 登录成功后自动关闭弹窗，用户留在当前页面
 * 4. 支持嵌套路由
 *
 * 使用示例：
 * ```tsx
 * <Route path="/chat" element={<ProtectedRoute><Chat /></ProtectedRoute>} />
 * ```
 */

import { useEffect, useRef, type ReactNode } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAuthStore } from '../../stores/useAuthStore';
import { useAuthModalStore } from '../../stores/useAuthModalStore';

interface ProtectedRouteProps {
  children: ReactNode;
}

export default function ProtectedRoute({ children }: ProtectedRouteProps) {
  const navigate = useNavigate();
  const { isAuthenticated } = useAuthStore();
  const { isOpen, openLogin } = useAuthModalStore();
  const wasOpenRef = useRef(false);

  // 监听弹窗关闭事件
  useEffect(() => {
    // 如果弹窗从打开变为关闭，且用户仍未登录，跳转到首页
    if (wasOpenRef.current && !isOpen && !isAuthenticated) {
      navigate('/', { replace: true });
    }
    wasOpenRef.current = isOpen;
  }, [isOpen, isAuthenticated, navigate]);

  // 未登录时触发登录弹窗
  useEffect(() => {
    if (!isAuthenticated) {
      openLogin();
    }
  }, [isAuthenticated, openLogin]);

  if (!isAuthenticated) {
    // 未登录时不渲染受保护内容，显示空白或占位符
    return null;
  }

  // 已登录，正常渲染子组件
  return <>{children}</>;
}
