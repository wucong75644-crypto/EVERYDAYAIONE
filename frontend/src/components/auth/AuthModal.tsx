/**
 * 认证弹窗容器组件
 *
 * 整合登录和注册表单，根据 mode 切换显示
 * 使用 useAuthModalStore 管理状态
 */

import { useMemo } from 'react';
import Modal from '../common/Modal';
import LoginForm from './LoginForm';
import RegisterForm from './RegisterForm';
import { useAuthModalStore } from '../../stores/useAuthModalStore';

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
      title={mode === 'login' ? '用户登录' : '用户注册'}
      maxWidth="max-w-md"
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
    </Modal>
  );
}
