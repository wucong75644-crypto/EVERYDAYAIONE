/**
 * 管理后台 — 整页路由组件（不再是 Modal）
 *
 * 按角色动态显示功能模块：
 * - super_admin: 平台、企业、监控和 Skill 管理；组织管理员另可管理账号、快麦
 * - owner/admin: 企业、账号连接、快麦和个人/组织 Skill 管理
 * - 普通成员: 个人 Skill 管理
 *
 * 路由：/admin
 * 历史：原先是 Modal，2026-06-09 改造成整页路由 + 合并快麦接入模块
 */

import { lazy, Suspense, useCallback, useRef, useState } from 'react';
import type { SkillNavigationState } from './skills/presentation';
import { useAuthStore } from '../../stores/useAuthStore';
import SuperAdminPanel from './SuperAdminPanel';
import OrgManagePanel from './OrgManagePanel';
import ReachConnections from '../settings/ReachConnections';
import KuaimaiIntegrationPanel from '../integrations/KuaimaiIntegrationPanel';

const SkillAdminPanel = lazy(() => import('./SkillAdminPanel'));

const ErrorMonitorPanel = lazy(() => import('./ErrorMonitorPanel'));
const UserManagePanel = lazy(() => import('./UserManagePanel'));

type Tab = 'platform' | 'org' | 'monitoring' | 'kuaimai' | 'users' | 'skills' | 'accounts';


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
  const availableSkillScopes = [
    'personal' as const,
    ...(isOrgAdmin ? ['org' as const] : []),
    ...(isSuperAdmin ? ['platform' as const] : []),
  ];

  const tabs: { key: Tab; label: string; visible: boolean }[] = [
    { key: 'platform', label: '平台管理', visible: isSuperAdmin },
    { key: 'users', label: '用户管理', visible: isSuperAdmin },
    { key: 'skills', label: '我的 Skill', visible: true },
    { key: 'accounts', label: '账号连接', visible: isOrgAdmin },
    { key: 'org', label: '企业管理', visible: isOrgAdmin || isSuperAdmin },
    { key: 'monitoring', label: '系统监控', visible: isSuperAdmin },
    { key: 'kuaimai', label: '🔗 快麦接入', visible: isOrgAdmin },
  ];
  const visibleTabs = tabs.filter((t) => t.visible);

  const requestedTab = new URLSearchParams(window.location.search).get('tab');
  const [activeTab, setActiveTab] = useState<Tab>(
    visibleTabs.find(tab => tab.key === requestedTab)?.key
      ?? (isSuperAdmin ? 'platform' : isOrgAdmin ? 'org' : 'skills'),
  );
  const selectedTab = visibleTabs.find(tab => tab.key === activeTab)?.key ?? visibleTabs[0].key;

  return (
    <div className={`min-h-screen ${visibleTabs.length > 1 ? "md:grid md:grid-cols-[220px_minmax(0,1fr)]" : ""}`}>
      {/* Tab 栏（≥ 2 个 tab 才显示）*/}
      {visibleTabs.length > 1 && (
        <div className="flex flex-wrap gap-1 border-b border-[var(--s-border-default)] bg-[var(--s-bg-primary)] p-4 md:flex-col md:items-stretch md:justify-start md:border-b-0 md:border-r" aria-label="后台导航">
          <div className="mb-8 hidden px-3 md:block"><p className="text-xl font-bold">EVERYDAYAI</p><p className="mt-1 text-sm text-[var(--s-text-secondary)]">管理后台</p></div>
          {visibleTabs.map((tab) => (
            <button
              key={tab.key}
              type="button"
              onClick={() => {
                if (tab.key === selectedTab || skillNavigation.current.busy) return;
                if (skillNavigation.current.dirty && !window.confirm('Skill 有未保存的修改。放弃修改并离开？')) return;
                setActiveTab(tab.key);
                const url = new URL(window.location.href);
                url.searchParams.set('tab', tab.key);
                window.history.replaceState(null, '', url);
              }}
              className={`rounded-lg px-4 py-3 text-left text-sm font-medium transition-colors ${
                selectedTab === tab.key
                  ? 'bg-[var(--s-bg-secondary)] text-[var(--s-accent)]'
                  : 'border-transparent text-[var(--s-text-secondary)] hover:text-[var(--s-text-primary)]'
              }`}
            >
              {tab.label}
            </button>
          ))}
        </div>
      )}

      {/* Tab 内容（保留 lazy 加载，避免不可见 tab 拖慢首屏） */}
      <div className="min-w-0 bg-[var(--s-bg-secondary)] p-4 sm:p-6 lg:p-8">
        <div className="mb-6 flex items-center justify-between gap-3 text-sm text-[var(--s-text-secondary)]"><span>管理后台 / {visibleTabs.find(tab => tab.key === selectedTab)?.label}</span><span>{currentOrg?.name || '未选择组织'}</span></div>
        {selectedTab === 'accounts' && isOrgAdmin && <ReachConnections key={currentOrg?.org_id} />}
        {selectedTab === 'platform' && isSuperAdmin && <SuperAdminPanel />}
        {selectedTab === 'users' && isSuperAdmin && (
          <Suspense fallback={<div className="text-center py-8 text-text-tertiary">加载中...</div>}>
            <UserManagePanel />
          </Suspense>
        )}
        {selectedTab === 'org' && (isOrgAdmin || isSuperAdmin) && (
          <OrgManagePanel orgId={currentOrg?.org_id} allowMcpManagement={isOrgAdmin} />
        )}
        {selectedTab === 'skills' && (
          <Suspense fallback={<div>加载中...</div>}>
            <SkillAdminPanel key={`${currentOrg?.org_id ?? 'personal'}:${availableSkillScopes.join(',')}`}
              orgId={currentOrg?.org_id} availableScopes={availableSkillScopes}
              onNavigationStateChange={updateSkillNavigation} />
          </Suspense>
        )}
        {selectedTab === 'monitoring' && isSuperAdmin && (
          <Suspense fallback={<div className="text-center py-8 text-text-tertiary">加载中...</div>}>
            <ErrorMonitorPanel />
          </Suspense>
        )}
        {selectedTab === 'kuaimai' && isOrgAdmin && (
          <KuaimaiIntegrationPanel />
        )}
      </div>
    </div>
  );
}
