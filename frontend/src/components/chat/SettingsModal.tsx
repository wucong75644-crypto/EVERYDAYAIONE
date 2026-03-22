/**
 * 个人设置弹框
 *
 * 显示用户信息和账户操作
 */

import { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAuthStore } from '../../stores/useAuthStore';
import { getWecomBindingStatus, unbindWecom } from '../../services/auth';
import WecomQrLogin from '../auth/WecomQrLogin';
import Modal from '../common/Modal';

interface SettingsModalProps {
  isOpen: boolean;
  onClose: () => void;
}

export default function SettingsModal({ isOpen, onClose }: SettingsModalProps) {
  const navigate = useNavigate();
  const { user, clearAuth, refreshUser } = useAuthStore();

  const [wecomStatus, setWecomStatus] = useState<{
    bound: boolean;
    wecom_nickname: string | null;
    bound_at: string | null;
  } | null>(null);
  const [wecomLoading, setWecomLoading] = useState(false);
  const [showWecomQr, setShowWecomQr] = useState(false);

  useEffect(() => {
    if (isOpen) {
      getWecomBindingStatus()
        .then(setWecomStatus)
        .catch(() => setWecomStatus(null));
    }
  }, [isOpen]);

  const handleWecomBind = () => {
    setShowWecomQr(true);
  };

  const handleWecomUnbind = async () => {
    if (!confirm('确定要解绑企业微信吗？')) return;
    setWecomLoading(true);
    try {
      await unbindWecom();
      setWecomStatus({ bound: false, wecom_nickname: null, bound_at: null });
      await refreshUser();
    } catch (err: unknown) {
      const error = err as { response?: { data?: { detail?: string } } };
      alert(error.response?.data?.detail || '解绑失败');
    } finally {
      setWecomLoading(false);
    }
  };

  const handleLogout = () => {
    clearAuth();
    onClose();
    navigate('/');
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

  return (
    <Modal
      isOpen={isOpen}
      onClose={onClose}
      closeOnOverlay={true}
      closeOnEsc={true}
      showCloseButton={false}
      maxWidth="max-w-md"
    >
      {/* 顶部标题栏 */}
      <div className="flex items-center justify-between pb-3 border-b border-gray-100 -mt-5 -mx-5 px-5 pt-5">
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
            onClick={onClose}
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
      <div className="space-y-4 mt-4">
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

        {/* 企微绑定 */}
        <div className="bg-white rounded-xl border border-gray-100 p-3">
          <div className="flex items-center justify-between">
            <div className="flex items-center space-x-3">
              <svg className="w-5 h-5 text-blue-500" viewBox="0 0 24 24" fill="currentColor">
                <path d="M8.691 2.188C3.891 2.188 0 5.476 0 9.53c0 2.212 1.17 4.203 3.002 5.55a.59.59 0 01.213.665l-.39 1.48c-.019.07-.048.141-.048.213 0 .163.13.295.29.295a.326.326 0 00.167-.054l1.903-1.114a.864.864 0 01.717-.098 10.16 10.16 0 002.837.403c.276 0 .543-.027.811-.05-.857-2.578.157-4.972 1.932-6.446 1.703-1.415 3.882-1.98 5.853-1.838-.576-3.583-4.196-6.348-8.596-6.348zM5.785 5.991c.642 0 1.162.529 1.162 1.18a1.17 1.17 0 01-1.162 1.178A1.17 1.17 0 014.623 7.17c0-.651.52-1.18 1.162-1.18zm5.813 0c.642 0 1.162.529 1.162 1.18a1.17 1.17 0 01-1.162 1.178 1.17 1.17 0 01-1.162-1.178c0-.651.52-1.18 1.162-1.18z" />
              </svg>
              <div>
                <span className="text-gray-700 text-sm">企业微信</span>
                {wecomStatus?.bound && wecomStatus.wecom_nickname && (
                  <p className="text-xs text-gray-400">{wecomStatus.wecom_nickname}</p>
                )}
              </div>
            </div>
            {wecomStatus?.bound ? (
              <button
                onClick={handleWecomUnbind}
                disabled={wecomLoading}
                className="text-sm text-red-500 hover:text-red-600 disabled:opacity-50"
              >
                {wecomLoading ? '处理中...' : '解绑'}
              </button>
            ) : (
              <button
                onClick={handleWecomBind}
                disabled={wecomLoading}
                className="text-sm text-blue-600 hover:text-blue-500 disabled:opacity-50"
              >
                绑定
              </button>
            )}
          </div>
          {/* 企微二维码（弹窗内展示） */}
          {showWecomQr && !wecomStatus?.bound && (
            <div className="mt-3 pt-3 border-t border-gray-100">
              <WecomQrLogin onBack={() => setShowWecomQr(false)} />
            </div>
          )}
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
    </Modal>
  );
}
