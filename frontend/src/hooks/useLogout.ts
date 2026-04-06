/**
 * 退出登录 Hook
 *
 * 统一退出逻辑：清除认证状态，企业用户跳回企业登录页，散客跳回首页。
 */

import { useAuthStore } from '../stores/useAuthStore';

export function useLogout() {
  const { clearAuth } = useAuthStore();

  return () => {
    // 优先用 login_org_id，兜底用 current_org_id（老版本登录的企业用户可能没有 login_org_id）
    const orgId = localStorage.getItem('login_org_id') || localStorage.getItem('current_org_id');
    clearAuth();
    window.location.href = orgId ? `/?org=${orgId}` : '/';
  };
}
