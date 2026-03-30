/**
 * 邀请通知组件
 *
 * 登录后自动检查是否有待接受的企业邀请。
 * 有邀请时显示通知条，用户可接受或忽略。
 */

import { useState, useEffect } from 'react';
import {
  listPendingInvitations,
  acceptInvitation,
  type PendingInvitation,
} from '../../services/org';
import { useAuthStore } from '../../stores/useAuthStore';

export default function InvitationNotice() {
  const { isAuthenticated, fetchOrganizations } = useAuthStore();
  const [invitations, setInvitations] = useState<PendingInvitation[]>([]);
  const [accepting, setAccepting] = useState<string | null>(null);

  useEffect(() => {
    if (isAuthenticated) {
      checkInvitations();
    }
  }, [isAuthenticated]);

  const checkInvitations = async () => {
    try {
      const data = await listPendingInvitations();
      setInvitations(data);
    } catch {
      // 静默失败
    }
  };

  const handleAccept = async (inv: PendingInvitation) => {
    setAccepting(inv.invite_token);
    try {
      await acceptInvitation(inv.invite_token);
      setInvitations((prev) => prev.filter((i) => i.invite_token !== inv.invite_token));
      // 刷新企业列表
      await fetchOrganizations();
    } catch {
      // ignore
    } finally {
      setAccepting(null);
    }
  };

  const handleDismiss = (token: string) => {
    setInvitations((prev) => prev.filter((i) => i.invite_token !== token));
  };

  if (invitations.length === 0) return null;

  return (
    <div className="fixed top-4 right-4 z-50 space-y-2 max-w-sm">
      {invitations.map((inv) => (
        <div
          key={inv.invite_token}
          className="bg-white rounded-lg shadow-lg border border-blue-200 p-4 animate-in fade-in"
        >
          <p className="text-sm text-gray-900 mb-2">
            <span className="font-medium text-blue-600">{inv.org_name}</span>
            {' '}邀请你加入，角色：
            {inv.role === 'admin' ? '管理员' : '成员'}
          </p>
          <div className="flex space-x-2">
            <button
              onClick={() => handleAccept(inv)}
              disabled={accepting === inv.invite_token}
              className="flex-1 py-1.5 text-sm bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:opacity-50 transition-colors"
            >
              {accepting === inv.invite_token ? '加入中...' : '接受邀请'}
            </button>
            <button
              onClick={() => handleDismiss(inv.invite_token)}
              className="px-3 py-1.5 text-sm text-gray-500 hover:text-gray-700 transition-colors"
            >
              忽略
            </button>
          </div>
        </div>
      ))}
    </div>
  );
}
