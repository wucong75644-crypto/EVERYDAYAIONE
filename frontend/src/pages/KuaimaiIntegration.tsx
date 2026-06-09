/**
 * 快麦 Web 数据接入 — 页面入口
 *
 * 路由：/settings/integrations/kuaimai
 * 权限：仅企业 owner/admin 可见入口（后端 _require_admin 是真正的安全边界）
 */

import { useNavigate } from 'react-router-dom';
import { ArrowLeft } from 'lucide-react';
import { useAuthStore } from '../stores/useAuthStore';
import LoadingScreen from '../components/common/LoadingScreen';
import KuaimaiIntegrationPanel from '../components/integrations/KuaimaiIntegrationPanel';


export default function KuaimaiIntegration() {
  const navigate = useNavigate();
  const currentOrg = useAuthStore((s) => s.currentOrg);
  const isAuthLoading = useAuthStore((s) => s.isLoading);

  if (isAuthLoading) return <LoadingScreen message="加载中..." />;

  if (!currentOrg) {
    navigate('/chat', { replace: true });
    return null;
  }

  return (
    <div className="min-h-screen bg-[var(--s-bg-primary)] p-6">
      <div className="max-w-5xl mx-auto">
        {/* 顶部 */}
        <div className="flex items-center gap-3 mb-6">
          <button
            type="button"
            onClick={() => navigate(-1)}
            className="p-2 rounded hover:bg-[var(--s-bg-secondary)]"
          >
            <ArrowLeft className="w-5 h-5" />
          </button>
          <div>
            <h1 className="text-xl font-semibold">🔗 快麦数据接入</h1>
            <p className="text-sm text-[var(--s-text-secondary)] mt-0.5">
              管理快麦智库 + 销售主题报表的自动同步
            </p>
          </div>
        </div>

        <KuaimaiIntegrationPanel />
      </div>
    </div>
  );
}
