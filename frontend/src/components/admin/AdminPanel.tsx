/**
 * 管理面板
 *
 * 按角色动态显示功能模块：
 * - super_admin: 企业列表 + 创建企业 + 本企业管理
 * - owner/admin: 本企业管理（ERP凭证 + 成员 + 信息）
 * - member/散客: 不显示（入口不可见）
 */

import { useState } from 'react';
import { useAuthStore } from '../../stores/useAuthStore';
import SuperAdminPanel from './SuperAdminPanel';
import OrgManagePanel from './OrgManagePanel';

interface AdminPanelProps {
  onClose: () => void;
}

export default function AdminPanel({ onClose }: AdminPanelProps) {
  const { user, currentOrg } = useAuthStore();

  const isSuperAdmin = user?.role === 'super_admin';
  const isOrgAdmin = currentOrg && ['owner', 'admin'].includes(currentOrg.role);

  type Tab = 'platform' | 'org';
  const [activeTab, setActiveTab] = useState<Tab>(isSuperAdmin ? 'platform' : 'org');

  const tabs: { key: Tab; label: string; visible: boolean }[] = [
    { key: 'platform', label: '平台管理', visible: isSuperAdmin },
    { key: 'org', label: '企业管理', visible: !!isOrgAdmin || isSuperAdmin },
  ];

  const visibleTabs = tabs.filter((t) => t.visible);

  return (
    <div className="fixed inset-0 z-50 bg-black/50 flex items-center justify-center p-4">
      <div className="bg-surface-card rounded-xl shadow-2xl w-full max-w-3xl max-h-[85vh] flex flex-col">
        {/* 头部 */}
        <div className="flex items-center justify-between px-6 py-4 border-b">
          <h2 className="text-lg font-semibold text-text-primary">管理后台</h2>
          <button
            onClick={onClose}
            className="p-1 rounded-lg hover:bg-hover text-text-disabled hover:text-text-tertiary transition-base"
          >
            <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>

        {/* Tab 栏 */}
        {visibleTabs.length > 1 && (
          <div className="flex border-b px-6">
            {visibleTabs.map((tab) => (
              <button
                key={tab.key}
                className={`px-4 py-2.5 text-sm font-medium transition-base ${
                  activeTab === tab.key
                    ? 'text-accent border-b-2 border-accent'
                    : 'text-text-tertiary hover:text-text-secondary'
                }`}
                onClick={() => setActiveTab(tab.key)}
              >
                {tab.label}
              </button>
            ))}
          </div>
        )}

        {/* 内容区 */}
        <div className="flex-1 overflow-y-auto p-6">
          {activeTab === 'platform' && isSuperAdmin && <SuperAdminPanel />}
          {activeTab === 'org' && (isOrgAdmin || isSuperAdmin) && (
            <OrgManagePanel orgId={currentOrg?.org_id} />
          )}
          {!isOrgAdmin && !isSuperAdmin && activeTab === 'org' && (
            <div className="text-center text-text-tertiary py-12">
              <p>当前未加入任何企业，或您不是管理员</p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
