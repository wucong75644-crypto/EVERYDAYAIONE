/**
 * 退出登录 Hook
 *
 * 统一退出逻辑：服务端吊销 refresh token → 清除认证状态 → 跳转。
 */

import { useAuthStore } from '../stores/useAuthStore';
import { API_BASE_URL } from '../services/api';

export function useLogout() {
  const { clearAuth } = useAuthStore();

  return () => {
    // 优先用 login_org_id，兜底用 current_org_id
    const orgId = localStorage.getItem('login_org_id') || localStorage.getItem('current_org_id');

    // 通知后端吊销 refresh token（fire-and-forget，不阻塞退出）
    const refreshToken = localStorage.getItem('refresh_token');
    if (refreshToken) {
      fetch(`${API_BASE_URL}/auth/logout`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ refresh_token: refreshToken }),
      }).catch(() => { /* 吊销失败不影响退出 */ });
    }

    clearAuth();
    window.location.href = orgId ? `/?org=${orgId}` : '/';
  };
}
