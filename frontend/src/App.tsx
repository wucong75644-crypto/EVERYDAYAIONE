import { useEffect, lazy, Suspense } from 'react';
import { BrowserRouter, Routes, Route, Navigate, useLocation } from 'react-router-dom';
import { AnimatePresence } from 'framer-motion';
import { Toaster } from 'react-hot-toast';
import { useAuthStore } from './stores/useAuthStore';
import { WebSocketProvider } from './contexts/WebSocketContext';
import ProtectedRoute from './components/auth/ProtectedRoute';
import AuthModal from './components/auth/AuthModal';
import LoadingScreen from './components/common/LoadingScreen';
import ErrorBoundary from './components/common/ErrorBoundary';

/**
 * V3 Phase 12：路由懒加载（架构隐患 3 修复）
 *
 * 4 个页面用 React.lazy 拆分代码块，首屏只下载当前页 + 公共代码。
 * Home 是首页一定下，但 Chat/ForgotPassword/WecomCallback 按需加载。
 *
 * Vite/Rollup 会自动给每个 lazy 页面创建独立 chunk。
 * Suspense fallback 用 LoadingScreen 提供过渡。
 */
const Home = lazy(() => import('./pages/Home'));
const Chat = lazy(() => import('./pages/Chat'));
const ForgotPassword = lazy(() => import('./pages/ForgotPassword'));
const WecomCallback = lazy(() => import('./pages/WecomCallback'));
const OrganizationSettings = lazy(() => import('./pages/OrganizationSettings'));

/**
 * 路由动画包装器
 * - useLocation 拿当前 location（key 用于触发 AnimatePresence enter/exit）
 * - <AnimatePresence mode="wait"> 让旧页面退场动画播完再 mount 新页面
 * - 必须放在 <BrowserRouter> 内部才能调用 useLocation
 *
 * 关键：route key 用"路由段"而非完整 pathname，避免在动态参数路由内
 * 切换时整个页面 unmount（例如 /chat/abc → /chat/xyz 应该是 Chat 内部
 * 状态变化，而不是整页 fade in/out）。
 */
export function getRouteKey(pathname: string): string {
  // 取第一段作为 key：'/' / '/chat' / '/forgot-password' / '/auth' 等
  // /chat 和 /chat/xxx 都是 'chat'，不会触发 unmount
  const seg = pathname.split('/').filter(Boolean)[0] || '';
  return '/' + seg;
}

function AnimatedRoutes() {
  const location = useLocation();
  const routeKey = getRouteKey(location.pathname);

  return (
    <Suspense fallback={<LoadingScreen message="加载中..." />}>
      <AnimatePresence mode="wait" initial={false}>
        <Routes location={location} key={routeKey}>
          <Route path="/" element={<Home />} />
          <Route path="/forgot-password" element={<ForgotPassword />} />
          <Route path="/auth/wecom/callback" element={<WecomCallback />} />
          {/* 受保护的路由：需要登录才能访问 */}
          <Route
            path="/chat"
            element={
              <ProtectedRoute>
                <Chat />
              </ProtectedRoute>
            }
          />
          <Route
            path="/chat/:id"
            element={
              <ProtectedRoute>
                <Chat />
              </ProtectedRoute>
            }
          />
          <Route
            path="/settings/organization"
            element={
              <ProtectedRoute>
                <OrganizationSettings />
              </ProtectedRoute>
            }
          />
          {/* 未匹配路由重定向到首页 */}
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </AnimatePresence>
    </Suspense>
  );
}

function App() {
  const { initAuth, isLoading } = useAuthStore();

  useEffect(() => {
    initAuth();
  }, [initAuth]);

  // 认证状态初始化中，显示加载屏幕（避免路由闪烁）
  if (isLoading) {
    return <LoadingScreen message="初始化中..." />;
  }

  return (
    <ErrorBoundary>
    <BrowserRouter>
      <WebSocketProvider>
        {/* 全局认证弹窗 */}
        <AuthModal />

        {/* 全局 Toast 通知（样式跟随主题 token） */}
        <Toaster
          position="top-center"
          toastOptions={{
            duration: 3000,
            style: {
              background: 'var(--color-surface-card)',
              color: 'var(--color-text-primary)',
              border: '1px solid var(--color-border-default)',
              borderRadius: 'var(--radius-lg)',
              boxShadow: 'var(--shadow-lg)',
              fontFamily: 'var(--font-body)',
              fontSize: '14px',
            },
            success: {
              iconTheme: {
                primary: 'var(--color-success)',
                secondary: 'var(--color-surface-card)',
              },
            },
            error: {
              iconTheme: {
                primary: 'var(--color-error)',
                secondary: 'var(--color-surface-card)',
              },
            },
          }}
        />

        <AnimatedRoutes />
      </WebSocketProvider>
    </BrowserRouter>
    </ErrorBoundary>
  );
}

export default App;
