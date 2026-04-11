/**
 * 认证弹窗容器组件
 *
 * 整合登录和注册表单，根据 mode 切换显示
 * 使用 useAuthModalStore 管理状态
 */

import { useMemo } from 'react';
import { AnimatePresence, m } from 'framer-motion';
import Modal from '../common/Modal';
import LoginForm from './LoginForm';
import RegisterForm from './RegisterForm';
import { useAuthModalStore } from '../../stores/useAuthModalStore';
import { APPLE_EASE, EXIT_EASE } from '../../utils/motion';

export default function AuthModal() {
  const { isOpen, mode, close, switchMode } = useAuthModalStore();

  // 从 URL 读取企业 ID（企业专属登录链接 ?org=xxx）
  const urlOrgId = useMemo(
    () => new URLSearchParams(window.location.search).get('org') || undefined,
    [],
  );

  const handleSuccess = () => {
    // 登录/注册成功后关闭弹窗
    close();
  };

  const handleSwitchMode = () => {
    switchMode();
  };

  return (
    <Modal
      isOpen={isOpen}
      onClose={close}
      title={mode === 'login' ? (urlOrgId ? '企业登录' : '用户登录') : '用户注册'}
      maxWidth="max-w-md"
    >
      {/* V3：登录/注册切换用 framer crossfade
          AnimatePresence mode="wait" 确保旧表单退出后再插入新表单
          mode 作为 key 触发 enter/exit */}
      <AnimatePresence mode="wait">
        <m.div
          key={mode}
          initial={{ opacity: 0, y: 8 }}
          animate={{ opacity: 1, y: 0, transition: APPLE_EASE }}
          exit={{ opacity: 0, y: -8, transition: EXIT_EASE }}
        >
          {mode === 'login' ? (
            <LoginForm
              onSuccess={handleSuccess}
              onSwitchToRegister={handleSwitchMode}
              orgId={urlOrgId}
            />
          ) : (
            <RegisterForm
              onSuccess={handleSuccess}
              onSwitchToLogin={handleSwitchMode}
            />
          )}
        </m.div>
      </AnimatePresence>
    </Modal>
  );
}
