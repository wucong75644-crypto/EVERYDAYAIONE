/**
 * 组织管理面板 — 独立路由 /settings/organization
 *
 * 设计：URL 可分享 + Modal 弹层 UI
 * - 路由 lazy 加载（与 Home/Chat 一致的代码分割模式）
 * - Modal 总是 isOpen=true，onClose 时 navigate(-1) 回退（保留浏览器历史）
 * - 实际内容渲染在 OrganizationModal，权限/tab/列表都在那里
 *
 * 权限：仅企业用户 + 管理员（owner/admin）可见入口
 * （后端 _require_admin 是真正的安全边界，前端 hide 只是 UX）
 */
import { useNavigate } from 'react-router-dom';
import { useAuthStore } from '../stores/useAuthStore';
import OrganizationModal from '../components/settings/OrganizationModal';
import LoadingScreen from '../components/common/LoadingScreen';

export default function OrganizationSettings() {
  const navigate = useNavigate();
  const currentOrg = useAuthStore((s) => s.currentOrg);
  const isAuthLoading = useAuthStore((s) => s.isLoading);

  // 1. 认证还在初始化
  if (isAuthLoading) {
    return <LoadingScreen message="加载中..." />;
  }

  // 2. 没有企业上下文（散客）→ 回退
  if (!currentOrg) {
    navigate('/chat', { replace: true });
    return null;
  }

  return (
    <OrganizationModal
      isOpen={true}
      onClose={() => navigate(-1)}
    />
  );
}
