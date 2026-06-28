/**
 * 用户详情抽屉 — 右侧滑入，含 3 Tab
 *
 * Tab 1: 积分（CreditsTab）
 * Tab 2: 会话视图（ConversationViewTab）— 按聊天记录顺序查看
 * Tab 3: 图片空间（AssetSpaceTab）— 上传/生成资产 + 批量 ZIP
 *
 * 关闭动画范式抄自 ModelDetailDrawer.tsx
 */

import { useState, useEffect, useCallback } from 'react';
import { X } from 'lucide-react';
import toast from 'react-hot-toast';
import { Badge } from '../ui/Badge';
import {
  getAdminUserSummary,
  type AdminUserSummary,
} from '../../services/adminUser';
import CreditsTab from './userDetail/CreditsTab';
import ConversationViewTab from './userDetail/ConversationViewTab';
import AssetSpaceTab from './userDetail/AssetSpaceTab';

type Tab = 'credits' | 'conversations' | 'assets';

interface UserDetailDrawerProps {
  userId: string;
  onClose: () => void;
  onChanged?: () => void;  // 积分变更后通知外层刷新列表
}

export default function UserDetailDrawer({ userId, onClose, onChanged }: UserDetailDrawerProps) {
  const [activeTab, setActiveTab] = useState<Tab>('credits');
  const [summary, setSummary] = useState<AdminUserSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [isClosing, setIsClosing] = useState(false);

  const loadSummary = useCallback(async () => {
    setLoading(true);
    try {
      const data = await getAdminUserSummary(userId);
      setSummary(data);
    } catch (err: any) {
      toast.error(err?.response?.data?.detail || '加载用户信息失败');
    } finally {
      setLoading(false);
    }
  }, [userId]);

  useEffect(() => {
    loadSummary();
    document.body.style.overflow = 'hidden';
    return () => { document.body.style.overflow = ''; };
  }, [loadSummary]);

  const handleClose = useCallback(() => {
    setIsClosing(true);
    setTimeout(() => {
      document.body.style.overflow = '';
      onClose();
    }, 150);
  }, [onClose]);

  // ESC 关闭
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') handleClose();
    };
    document.addEventListener('keydown', handler);
    return () => document.removeEventListener('keydown', handler);
  }, [handleClose]);

  const handleCreditsChanged = () => {
    loadSummary();
    onChanged?.();
  };

  const tabs: { key: Tab; label: string }[] = [
    { key: 'credits', label: '积分' },
    { key: 'conversations', label: '会话视图' },
    { key: 'assets', label: '图片空间' },
  ];

  return (
    <div className={`fixed inset-0 z-50 ${isClosing ? 'pointer-events-none' : ''}`}>
      {/* 遮罩 */}
      <div
        className={`absolute inset-0 bg-black/40 ${
          isClosing ? 'animate-backdrop-exit' : 'animate-backdrop-enter'
        }`}
        onClick={handleClose}
        aria-hidden="true"
      />

      {/* 面板 */}
      <div
        className={`fixed right-0 top-0 h-full w-full sm:w-[760px] bg-surface-card shadow-2xl flex flex-col ${
          isClosing ? 'animate-drawer-exit' : 'animate-drawer-enter'
        }`}
        role="dialog"
        aria-modal="true"
        aria-label="用户详情"
      >
        {/* 头部 */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-[var(--s-border-default)]">
          <div className="flex items-center gap-3 min-w-0">
            {summary?.avatar_url ? (
              <img
                src={summary.avatar_url}
                alt=""
                className="w-9 h-9 rounded-full object-cover shrink-0"
              />
            ) : (
              <div className="w-9 h-9 rounded-full bg-[var(--s-bg-tertiary)] flex items-center justify-center text-sm shrink-0">
                {summary?.nickname?.[0] || '?'}
              </div>
            )}
            <div className="min-w-0">
              <div className="font-semibold truncate">
                {loading ? '加载中...' : summary?.nickname || '—'}
              </div>
              <div className="text-xs text-[var(--s-text-tertiary)] flex items-center gap-2 mt-0.5">
                <span className="font-mono">{summary?.phone || '—'}</span>
                {summary?.role && (
                  <Badge size="sm" variant={summary.role === 'super_admin' ? 'accent' : 'default'}>
                    {summary.role}
                  </Badge>
                )}
                {summary?.org_name && (
                  <span className="text-[var(--s-text-secondary)]">· {summary.org_name}</span>
                )}
              </div>
            </div>
          </div>
          <button
            onClick={handleClose}
            className="p-2 hover:bg-[var(--s-hover)] rounded-lg transition-colors shrink-0"
            aria-label="关闭"
          >
            <X className="w-5 h-5 text-[var(--s-text-tertiary)]" />
          </button>
        </div>

        {/* 概览数字 */}
        {summary && (
          <div className="flex gap-6 px-6 py-3 border-b border-[var(--s-border-default)] bg-[var(--s-bg-secondary)]/30 text-sm">
            <div>
              <span className="text-[var(--s-text-tertiary)]">当前积分</span>{' '}
              <span className="font-mono font-semibold">{summary.credits}</span>
            </div>
            <div>
              <span className="text-[var(--s-text-tertiary)]">累计消耗</span>{' '}
              <span className="font-mono">{summary.total_consumed}</span>
            </div>
            <div>
              <span className="text-[var(--s-text-tertiary)]">对话数</span>{' '}
              <span className="font-mono">{summary.conversation_count}</span>
            </div>
            <div className="ml-auto text-[var(--s-text-tertiary)] text-xs">
              注册 {summary.created_at?.slice(0, 10)}
            </div>
          </div>
        )}

        {/* Tab 栏 */}
        <div className="flex border-b border-[var(--s-border-default)] px-6">
          {tabs.map((t) => (
            <button
              key={t.key}
              type="button"
              onClick={() => setActiveTab(t.key)}
              className={`px-4 py-2.5 text-sm font-medium border-b-2 transition-colors ${
                activeTab === t.key
                  ? 'border-[var(--s-accent)] text-[var(--s-accent)]'
                  : 'border-transparent text-[var(--s-text-secondary)] hover:text-[var(--s-text-primary)]'
              }`}
            >
              {t.label}
            </button>
          ))}
        </div>

        {/* Tab 内容 */}
        <div className="flex-1 overflow-y-auto p-6">
          {activeTab === 'credits' && (
            <CreditsTab
              userId={userId}
              balance={summary?.credits ?? 0}
              status={summary?.status}
              onChanged={handleCreditsChanged}
            />
          )}
          {activeTab === 'conversations' && <ConversationViewTab userId={userId} />}
          {activeTab === 'assets' && <AssetSpaceTab userId={userId} />}
        </div>
      </div>
    </div>
  );
}
