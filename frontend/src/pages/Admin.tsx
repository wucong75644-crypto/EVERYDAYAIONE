/**
 * 管理后台 — 整页路由入口
 *
 * 路由：/admin
 * 内含 4 个 tab（按权限可见性）：
 *   - 平台管理（super_admin）
 *   - 企业管理（super_admin / owner / admin）
 *   - 系统监控（super_admin）
 *   - 🔗 快麦接入（owner / admin）  ← 含子 tab：数据源 / 同步记录 / 运营管理
 *
 * 权限：未登录 → ProtectedRoute 拦截；其他角色 → AdminPanel 内部展示"无权限"
 */

import { useNavigate, useSearchParams } from 'react-router-dom';
import { ArrowLeft } from 'lucide-react';
import { useAuthStore } from '../stores/useAuthStore';
import LoadingScreen from '../components/common/LoadingScreen';
import AdminPanel from '../components/admin/AdminPanel';


export default function Admin() {
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const isAuthLoading = useAuthStore((s) => s.isLoading);

  if (isAuthLoading) return <LoadingScreen message="加载中..." />;

  // 支持 ?tab=kuaimai 这种深度链接（兼容旧 /settings/integrations/kuaimai 跳转）
  // AdminPanel 内部读 search params 自行处理（后续按需扩展）
  void params; // 防止 unused warning

  return (
    <div className="min-h-screen bg-[var(--s-bg-primary)] p-6">
      <div className="max-w-5xl mx-auto">
        {/* 顶部 */}
        <div className="flex items-center gap-3 mb-6">
          <button
            type="button"
            onClick={() => navigate(-1)}
            className="p-2 rounded hover:bg-[var(--s-bg-secondary)]"
            aria-label="返回"
          >
            <ArrowLeft className="w-5 h-5" />
          </button>
          <div>
            <h1 className="text-xl font-semibold">管理后台</h1>
          </div>
        </div>

        <AdminPanel />
      </div>
    </div>
  );
}
