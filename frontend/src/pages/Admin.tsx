/** 管理后台完整页面，模块按角色显示，支持 ?tab=accounts 等深度链接。 */

import { useNavigate } from 'react-router-dom';
import { useState } from 'react';
import type { SkillNavigationState } from '../components/admin/skills/presentation';
import { ArrowLeft } from 'lucide-react';
import { useAuthStore } from '../stores/useAuthStore';
import LoadingScreen from '../components/common/LoadingScreen';
import AdminPanel from '../components/admin/AdminPanel';


export default function Admin() {
  const navigate = useNavigate();
  const isAuthLoading = useAuthStore((s) => s.isLoading);
  const [skillNavigation, setSkillNavigation] = useState<SkillNavigationState>({ dirty: false, busy: false });

  if (isAuthLoading) return <LoadingScreen message="加载中..." />;

  return (
    <div className="min-h-screen bg-[var(--s-bg-primary)] text-[var(--s-text-primary)]">
      <div className="w-full">
        {/* 顶部 */}
        <div className="flex items-center gap-3 border-b border-[var(--s-border-default)] px-4 py-3">
          <button
            type="button"
            disabled={skillNavigation.busy}
            onClick={() => {
              if (skillNavigation.dirty && !window.confirm('Skill 有未保存的修改。放弃修改并离开？')) return;
              navigate(-1);
            }}
            className="p-2 rounded hover:bg-[var(--s-bg-secondary)]"
            aria-label="返回"
          >
            <ArrowLeft className="w-5 h-5" />
          </button>
          <div>
            <h1 className="text-xl font-semibold">管理后台</h1>
          </div>
        </div>

        <AdminPanel onSkillNavigationStateChange={setSkillNavigation} />
      </div>
    </div>
  );
}
