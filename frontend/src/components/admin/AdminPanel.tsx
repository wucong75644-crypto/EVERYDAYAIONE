/**
 * 管理后台 — 整页路由组件（不再是 Modal）
 *
 * 按角色动态显示功能模块：
 * - super_admin: 平台管理 / 企业管理 / 系统监控 / 快麦接入
 * - owner/admin: 企业管理 / 快麦接入
 * - member/散客: 不应进入这个页面（路由入口已隐藏）
 *
 * 路由：/admin
 * 历史：原先是 Modal，2026-06-09 改造成整页路由 + 合并快麦接入模块
 */

import { lazy, Suspense, useCallback, useRef, useState } from 'react';
import type { SkillNavigationState } from './skills/presentation';
import { useAuthStore } from '../../stores/useAuthStore';
import SuperAdminPanel from './SuperAdminPanel';
import OrgManagePanel from './OrgManagePanel';
import KuaimaiIntegrationPanel from '../integrations/KuaimaiIntegrationPanel';

const SkillAdminPanel = lazy(() => import('./SkillAdminPanel'));

const ErrorMonitorPanel = lazy(() => import('./ErrorMonitorPanel'));
const UserManagePanel = lazy(() => import('./UserManagePanel'));

type Tab = 'platform' | 'org' | 'monitoring' | 'kuaimai' | 'users' | 'skills';


export default function AdminPanel({ onSkillNavigationStateChange }: {
  onSkillNavigationStateChange?: (state: SkillNavigationState) => void;
}) {
  const { user, currentOrg } = useAuthStore();
  const skillNavigation = useRef<SkillNavigationState>({ dirty: false, busy: false });
  const updateSkillNavigation = useCallback((state: SkillNavigationState) => {
    skillNavigation.current = state;
    onSkillNavigationStateChange?.(state);
  }, [onSkillNavigationStateChange]);

  const isSuperAdmin = user?.role === 'super_admin';
  const isOrgAdmin = !!(currentOrg && ['owner', 'admin'].includes(currentOrg.role));

  const tabs: { key: Tab; label: string; visible: boolean }[] = [
    { key: 'platform', label: '平台管理', visible: isSuperAdmin },
    { key: 'users', label: '用户管理', visible: isSuperAdmin },
    { key: 'skills', label: 'Skill 管理', visible: isOrgAdmin },
    { key: 'org', label: '企业管理', visible: isOrgAdmin || isSuperAdmin },
    { key: 'monitoring', label: '系统监控', visible: isSuperAdmin },
    { key: 'kuaimai', label: '🔗 快麦接入', visible: isOrgAdmin },
  ];
  const visibleTabs = tabs.filter((t) => t.visible);

  // 默认 tab：超管→平台管理；普通管理员→企业管理
  const [activeTab, setActiveTab] = useState<Tab>(
    isSuperAdmin ? 'platform' : 'org',
  );

  if (visibleTabs.length === 0) {
    return (
      <div className="text-center text-text-tertiary py-12">
        <p>无管理权限</p>
      </div>
    );
  }

  return (
    <div className="flex flex-col h-full">
      {/* Tab 栏（≥ 2 个 tab 才显示）*/}
      {visibleTabs.length > 1 && (
        <div className="flex flex-wrap border-b border-[var(--s-border-default)] mb-4">
          {visibleTabs.map((tab) => (
            <button
              key={tab.key}
              type="button"
              onClick={() => {
                if (tab.key === activeTab || skillNavigation.current.busy) return;
                if (skillNavigation.current.dirty && !window.confirm('Skill 有未保存的修改。放弃修改并离开？')) return;
                setActiveTab(tab.key);
              }}
              className={`px-4 py-2.5 text-sm font-medium border-b-2 transition-colors ${
                activeTab === tab.key
                  ? 'border-[var(--s-accent)] text-[var(--s-accent)]'
                  : 'border-transparent text-[var(--s-text-secondary)] hover:text-[var(--s-text-primary)]'
              }`}
            >
              {tab.label}
            </button>
          ))}
        </div>
      )}

      {/* Tab 内容（保留 lazy 加载，避免不可见 tab 拖慢首屏） */}
      <div className="flex-1 overflow-y-auto">
        {activeTab === 'platform' && isSuperAdmin && <SuperAdminPanel />}
        {activeTab === 'users' && isSuperAdmin && (
          <Suspense fallback={<div className="text-center py-8 text-text-tertiary">加载中...</div>}>
            <UserManagePanel />
          </Suspense>
        )}
        {activeTab === 'org' && (isOrgAdmin || isSuperAdmin) && (
          <OrgManagePanel orgId={currentOrg?.org_id} />
        )}
        {activeTab === 'skills' && isOrgAdmin && currentOrg && (
          <Suspense fallback={<div>加载中...</div>}>
            <SkillAdminPanel key={currentOrg.org_id} orgId={currentOrg.org_id} onNavigationStateChange={updateSkillNavigation} />
          </Suspense>
        )}
        {activeTab === 'monitoring' && isSuperAdmin && (
          <Suspense fallback={<div className="text-center py-8 text-text-tertiary">加载中...</div>}>
            <ErrorMonitorPanel />
          </Suspense>
        )}
        {activeTab === 'kuaimai' && isOrgAdmin && (
          <KuaimaiIntegrationPanel />
        )}
      </div>
    </div>
  );
}
