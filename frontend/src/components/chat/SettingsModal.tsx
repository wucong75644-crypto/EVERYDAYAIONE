/**
 * 个人设置弹框
 *
 * 显示用户信息和账户操作
 */

import { useState, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAuthStore } from '../../stores/useAuthStore';

interface SettingsModalProps {
  isOpen: boolean;
  onClose: () => void;
}

export default function SettingsModal({ isOpen, onClose }: SettingsModalProps) {
  const navigate = useNavigate();
  const { user, clearAuth, refreshUser } = useAuthStore();
  const [isClosing, setIsClosing] = useState(false);

  // 处理关闭动画
  const handleClose = useCallback(() => {
    if (isClosing) return; // 防止重复触发
    setIsClosing(true);
    setTimeout(() => {
      setIsClosing(false);
      onClose();
    }, 150); // 动画时长
  }, [isClosing, onClose]);

  // 不渲染已关闭的模态框，同时重置关闭状态
  if (!isOpen) {
    if (isClosing) {
      // 安全地在下一帧重置状态（避免在渲染期间更新）
      requestAnimationFrame(() => setIsClosing(false));
    }
    return null;
  }

  const handleLogout = () => {
    clearAuth();
    onClose();
    navigate('/login');
  };

  const handleRefresh = async () => {
    await refreshUser();
  };

  // 格式化手机号（隐藏中间4位）
  const formatPhone = (phone: string | null | undefined): string => {
    if (!phone) return '未绑定';
    if (phone.length === 11) {
      return `${phone.slice(0, 3)}****${phone.slice(7)}`;
    }
    return phone;
  };

  // 格式化日期
  const formatDate = (dateStr: string | undefined): string => {
    if (!dateStr) return '未知';
    const date = new Date(dateStr);
    return date.toLocaleDateString('zh-CN', {
      year: 'numeric',
      month: 'long',
      day: 'numeric',
    });
  };

  // 点击遮罩关闭
  const handleBackdropClick = (e: React.MouseEvent) => {
    if (e.target === e.currentTarget) {
      handleClose();
    }
  };

  return (
    <div
      className={`fixed inset-0 z-50 flex items-center justify-center bg-black/50 transition-opacity duration-150 ${
        isClosing ? 'opacity-0' : 'animate-in fade-in duration-150'
      }`}
      onClick={handleBackdropClick}
    >
      <div
        className="bg-white rounded-2xl w-full max-w-md mx-4 max-h-[90vh] overflow-y-auto shadow-xl"
        style={{
          animation: isClosing
            ? 'modal-exit 150ms cubic-bezier(0.32, 0.72, 0, 1) forwards'
            : 'modal-enter 200ms cubic-bezier(0.32, 0.72, 0, 1)',
        }}
      >
        <style>{`
          @keyframes modal-enter {
            from {
              opacity: 0;
              transform: scale(0.96) translateY(8px);
            }
            to {
              opacity: 1;
              transform: scale(1) translateY(0);
            }
          }
          @keyframes modal-exit {
            from {
              opacity: 1;
              transform: scale(1) translateY(0);
            }
            to {
              opacity: 0;
              transform: scale(0.96) translateY(8px);
            }
          }
        `}</style>
        {/* 顶部标题栏 */}
        <div className="flex items-center justify-between p-4 border-b border-gray-100">
          <h2 className="text-lg font-semibold">个人设置</h2>
          <div className="flex items-center space-x-2">
            <button
              onClick={handleRefresh}
              className="p-2 hover:bg-gray-100 rounded-lg transition-colors"
              title="刷新信息"
            >
              <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
              </svg>
            </button>
            <button
              onClick={handleClose}
              className="p-2 hover:bg-gray-100 rounded-lg transition-colors"
              title="关闭"
            >
              <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
              </svg>
            </button>
          </div>
        </div>

        {/* 主内容 */}
        <div className="p-4 space-y-4">
          {/* 用户头像和基本信息 */}
          <div className="flex items-center space-x-4 p-4 bg-gray-50 rounded-xl">
            <div className="w-14 h-14 bg-blue-500 rounded-full flex items-center justify-center text-xl font-bold text-white">
              {user?.nickname?.charAt(0) || 'U'}
            </div>
            <div>
              <h3 className="text-lg font-semibold text-gray-900">{user?.nickname || '用户'}</h3>
              <p className="text-sm text-gray-500">
                {user?.role === 'admin' ? '管理员' : user?.role === 'super_admin' ? '超级管理员' : '普通用户'}
              </p>
            </div>
          </div>

          {/* 账户信息 */}
          <div className="bg-white rounded-xl border border-gray-100 divide-y divide-gray-100">
            <div className="p-3 flex items-center justify-between">
              <div className="flex items-center space-x-3">
                <svg className="w-5 h-5 text-gray-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M3 5a2 2 0 012-2h3.28a1 1 0 01.948.684l1.498 4.493a1 1 0 01-.502 1.21l-2.257 1.13a11.042 11.042 0 005.516 5.516l1.13-2.257a1 1 0 011.21-.502l4.493 1.498a1 1 0 01.684.949V19a2 2 0 01-2 2h-1C9.716 21 3 14.284 3 6V5z" />
                </svg>
                <span className="text-gray-700 text-sm">手机号</span>
              </div>
              <span className="text-gray-900 text-sm">{formatPhone(user?.phone)}</span>
            </div>

            <div className="p-3 flex items-center justify-between">
              <div className="flex items-center space-x-3">
                <svg className="w-5 h-5 text-gray-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 8c-1.657 0-3 .895-3 2s1.343 2 3 2 3 .895 3 2-1.343 2-3 2m0-8c1.11 0 2.08.402 2.599 1M12 8V7m0 1v8m0 0v1m0-1c-1.11 0-2.08-.402-2.599-1M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
                </svg>
                <span className="text-gray-700 text-sm">积分余额</span>
              </div>
              <span className="text-blue-600 font-medium text-sm">{user?.credits ?? 0} 积分</span>
            </div>

            <div className="p-3 flex items-center justify-between">
              <div className="flex items-center space-x-3">
                <svg className="w-5 h-5 text-gray-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 7V3m8 4V3m-9 8h10M5 21h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z" />
                </svg>
                <span className="text-gray-700 text-sm">注册时间</span>
              </div>
              <span className="text-gray-900 text-sm">{formatDate(user?.created_at)}</span>
            </div>
          </div>

          {/* 操作按钮 */}
          <button
            onClick={handleLogout}
            className="w-full py-3 bg-red-50 text-red-600 rounded-xl font-medium hover:bg-red-100 transition-colors"
          >
            退出登录
          </button>

          {/* 版本信息 */}
          <p className="text-center text-xs text-gray-400">每日AI v1.0.0</p>
        </div>
      </div>
    </div>
  );
}
