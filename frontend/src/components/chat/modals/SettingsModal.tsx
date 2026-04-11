/**
 * 个人设置弹框
 *
 * 显示用户信息和账户操作
 */

import { useState, useEffect } from 'react';
import { m, LayoutGroup } from 'framer-motion';
import { Palette, Sun, Moon, Monitor, Check } from 'lucide-react';
import { useAuthStore } from '../../../stores/useAuthStore';
import { useLogout } from '../../../hooks/useLogout';
import { useTheme, type ThemeName, type ColorMode } from '../../../hooks/useTheme';
import { getWecomBindingStatus, unbindWecom } from '../../../services/auth';
import WecomQrLogin from '../../auth/WecomQrLogin';
import Modal from '../../common/Modal';
import { cn } from '../../../utils/cn';
import { SOFT_SPRING } from '../../../utils/motion';

interface SettingsModalProps {
  isOpen: boolean;
  onClose: () => void;
}

/**
 * 主题风格选项
 * V3 Phase 13：每个主题带 vibe 描述 + 背景+强调色双色预览
 */
const THEME_OPTIONS: {
  value: ThemeName;
  label: string;
  vibe: string;
  preview: string;        // 强调色
  background: string;     // 卡片预览背景色
}[] = [
  {
    value: 'classic',
    label: '经典蓝',
    vibe: '通用商务',
    preview: '#2563eb',
    background: '#f9fafb',
  },
  {
    value: 'claude',
    label: 'Claude 暖色',
    vibe: '温暖文学',
    preview: '#c96442',
    background: '#f5f4ed',
  },
  {
    value: 'linear',
    label: 'Linear 工程',
    vibe: '暗夜精密',
    preview: '#5e6ad2',
    background: '#08090a',
  },
];

/** 明暗模式选项 */
const COLOR_MODE_OPTIONS: { value: ColorMode; label: string; icon: typeof Sun }[] = [
  { value: 'light', label: '浅色', icon: Sun },
  { value: 'dark', label: '深色', icon: Moon },
  { value: 'system', label: '跟随系统', icon: Monitor },
];

export default function SettingsModal({ isOpen, onClose }: SettingsModalProps) {
  const { user, currentOrg, refreshUser } = useAuthStore();
  const logout = useLogout();
  const { theme, colorMode, setTheme, setColorMode } = useTheme();

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
    onClose();
    logout();
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
      maxWidth={showWecomQr ? 'max-w-3xl' : 'max-w-md'}
    >
      {/* 顶部标题栏 */}
      <div className="flex items-center justify-between pb-3 border-b border-border-light -mt-5 -mx-5 px-5 pt-5">
        <h2 className="text-lg font-semibold">个人设置</h2>
        <div className="flex items-center space-x-2">
          <button
            onClick={handleRefresh}
            className="p-2 hover:bg-hover rounded-lg transition-base"
            title="刷新信息"
          >
            <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
            </svg>
          </button>
          <button
            onClick={onClose}
            className="p-2 hover:bg-hover rounded-lg transition-base"
            title="关闭"
          >
            <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>
      </div>

      {/* 主内容：展开二维码时左右布局，否则单列 */}
      <div className={showWecomQr ? 'flex gap-6 mt-4' : 'mt-4'}>
        {/* 左侧：用户信息 */}
        <div className={`space-y-4 ${showWecomQr ? 'flex-1 min-w-0' : ''}`}>
          {/* 用户头像和基本信息 */}
          <div className="flex items-center space-x-4 p-4 bg-surface rounded-xl">
            <div className="w-14 h-14 bg-accent rounded-full flex items-center justify-center text-xl font-bold text-text-on-accent shrink-0">
              {user?.nickname?.charAt(0) || 'U'}
            </div>
            <div>
              <h3 className="text-lg font-semibold text-text-primary">{user?.nickname || '用户'}</h3>
              <p className="text-sm text-text-tertiary">
                {user?.role === 'admin' ? '管理员' : user?.role === 'super_admin' ? '超级管理员' : '普通用户'}
                {currentOrg && (
                  <span className="ml-2 text-accent">{currentOrg.name}</span>
                )}
              </p>
            </div>
          </div>


          {/* 账户信息 */}
          <div className="bg-surface-card rounded-xl border border-border-light divide-y divide-border-light">
            <div className="p-3 flex items-center justify-between">
              <div className="flex items-center space-x-3">
                <svg className="w-5 h-5 text-text-disabled" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M3 5a2 2 0 012-2h3.28a1 1 0 01.948.684l1.498 4.493a1 1 0 01-.502 1.21l-2.257 1.13a11.042 11.042 0 005.516 5.516l1.13-2.257a1 1 0 011.21-.502l4.493 1.498a1 1 0 01.684.949V19a2 2 0 01-2 2h-1C9.716 21 3 14.284 3 6V5z" />
                </svg>
                <span className="text-text-secondary text-sm">手机号</span>
              </div>
              <span className="text-text-primary text-sm">{formatPhone(user?.phone)}</span>
            </div>

            <div className="p-3 flex items-center justify-between">
              <div className="flex items-center space-x-3">
                <svg className="w-5 h-5 text-text-disabled" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 8c-1.657 0-3 .895-3 2s1.343 2 3 2 3 .895 3 2-1.343 2-3 2m0-8c1.11 0 2.08.402 2.599 1M12 8V7m0 1v8m0 0v1m0-1c-1.11 0-2.08-.402-2.599-1M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
                </svg>
                <span className="text-text-secondary text-sm">积分余额</span>
              </div>
              <span className="text-accent font-medium text-sm">{user?.credits ?? 0} 积分</span>
            </div>

            <div className="p-3 flex items-center justify-between">
              <div className="flex items-center space-x-3">
                <svg className="w-5 h-5 text-text-disabled" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 7V3m8 4V3m-9 8h10M5 21h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z" />
                </svg>
                <span className="text-text-secondary text-sm">注册时间</span>
              </div>
              <span className="text-text-primary text-sm">{formatDate(user?.created_at)}</span>
            </div>
          </div>

          {/* 外观设置 */}
          <div className="bg-surface-card rounded-xl border border-border-light p-4">
            <div className="flex items-center space-x-2 mb-3">
              <Palette className="w-4 h-4 text-text-tertiary" />
              <h3 className="text-sm font-medium text-text-primary">外观</h3>
            </div>

            {/* 主题风格选择（V3：预览卡片 + layoutId Magic Move） */}
            <div className="mb-4">
              <label className="block text-xs text-text-tertiary mb-2">主题风格</label>
              <LayoutGroup id="settings-theme">
                <div className="grid grid-cols-3 gap-2">
                  {THEME_OPTIONS.map((opt) => {
                    const isActive = theme === opt.value;
                    return (
                      <m.button
                        key={opt.value}
                        type="button"
                        onClick={() => setTheme(opt.value)}
                        whileHover={{ y: -2 }}
                        whileTap={{ scale: 0.97 }}
                        transition={SOFT_SPRING}
                        className="relative flex flex-col items-start gap-1.5 p-3 rounded-lg text-left overflow-hidden"
                        aria-pressed={isActive}
                      >
                        {/* 选中框 — Magic Move layoutId */}
                        {isActive && (
                          <m.div
                            layoutId="theme-selected-ring"
                            className="absolute inset-0 rounded-lg border-2 border-accent pointer-events-none"
                            transition={SOFT_SPRING}
                          />
                        )}

                        {/* 主题色预览条 */}
                        <div
                          className="w-full h-12 rounded-md relative overflow-hidden border border-border-default"
                          style={{ backgroundColor: opt.background }}
                        >
                          <div
                            className="absolute right-2 top-1/2 -translate-y-1/2 w-6 h-6 rounded-full"
                            style={{ backgroundColor: opt.preview }}
                          />
                        </div>

                        {/* 标题 */}
                        <div className="w-full flex items-center justify-between">
                          <span className="text-xs font-medium text-text-primary">
                            {opt.label}
                          </span>
                          {isActive && <Check className="w-3 h-3 text-accent" />}
                        </div>

                        {/* Vibe 描述 */}
                        <span className="text-[10px] text-text-tertiary">
                          {opt.vibe}
                        </span>
                      </m.button>
                    );
                  })}
                </div>
              </LayoutGroup>
            </div>

            {/* 明暗模式切换 */}
            <div>
              <label className="block text-xs text-text-tertiary mb-2">外观模式</label>
              <div className="grid grid-cols-3 gap-2">
                {COLOR_MODE_OPTIONS.map(({ value, label, icon: Icon }) => (
                  <button
                    key={value}
                    type="button"
                    onClick={() => setColorMode(value)}
                    className={cn(
                      'flex flex-col items-center gap-1 px-2 py-2 rounded-lg border text-xs transition-base',
                      colorMode === value
                        ? 'border-accent bg-accent-light text-accent'
                        : 'border-border-default text-text-secondary hover:bg-hover',
                    )}
                    aria-pressed={colorMode === value}
                  >
                    <Icon className="w-4 h-4" />
                    <span>{label}</span>
                  </button>
                ))}
              </div>
            </div>
          </div>

          {/* 企微绑定 */}
          <div className="bg-surface-card rounded-xl border border-border-light p-3">
            <div className="flex items-center justify-between">
              <div className="flex items-center space-x-3">
                <svg className="w-5 h-5 text-accent" viewBox="0 0 24 24" fill="currentColor">
                  <path d="M8.691 2.188C3.891 2.188 0 5.476 0 9.53c0 2.212 1.17 4.203 3.002 5.55a.59.59 0 01.213.665l-.39 1.48c-.019.07-.048.141-.048.213 0 .163.13.295.29.295a.326.326 0 00.167-.054l1.903-1.114a.864.864 0 01.717-.098 10.16 10.16 0 002.837.403c.276 0 .543-.027.811-.05-.857-2.578.157-4.972 1.932-6.446 1.703-1.415 3.882-1.98 5.853-1.838-.576-3.583-4.196-6.348-8.596-6.348zM5.785 5.991c.642 0 1.162.529 1.162 1.18a1.17 1.17 0 01-1.162 1.178A1.17 1.17 0 014.623 7.17c0-.651.52-1.18 1.162-1.18zm5.813 0c.642 0 1.162.529 1.162 1.18a1.17 1.17 0 01-1.162 1.178 1.17 1.17 0 01-1.162-1.178c0-.651.52-1.18 1.162-1.18z" />
                </svg>
                <div>
                  <span className="text-text-secondary text-sm">企业微信</span>
                  {wecomStatus?.bound && wecomStatus.wecom_nickname && (
                    <p className="text-xs text-text-disabled">{wecomStatus.wecom_nickname}</p>
                  )}
                </div>
              </div>
              {wecomStatus?.bound ? (
                <button
                  onClick={handleWecomUnbind}
                  disabled={wecomLoading}
                  className="text-sm text-error hover:text-error/80 transition-base disabled:opacity-50"
                >
                  {wecomLoading ? '处理中...' : '解绑'}
                </button>
              ) : (
                <button
                  onClick={handleWecomBind}
                  className="text-sm text-accent hover:text-accent-hover transition-base"
                >
                  {showWecomQr ? '收起' : '绑定'}
                </button>
              )}
            </div>
          </div>

          {/* 操作按钮 */}
          <button
            onClick={handleLogout}
            className="w-full py-3 bg-error-light text-error rounded-xl font-medium hover:bg-error/15 transition-base"
          >
            退出登录
          </button>

          {/* 版本信息 */}
          <p className="text-center text-xs text-text-disabled">每日AI v1.0.0</p>
        </div>

        {/* 右侧：企微二维码（展开时显示） */}
        {showWecomQr && !wecomStatus?.bound && (
          <div className="w-[340px] shrink-0 border-l border-border-light pl-6">
            <WecomQrLogin mode="bind" onBack={() => setShowWecomQr(false)} />
          </div>
        )}
      </div>
    </Modal>
  );
}
