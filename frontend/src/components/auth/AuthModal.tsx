/**
 * 认证弹窗容器组件
 *
 * 整合登录和注册表单，根据 mode 切换显示
 * 使用 useAuthModalStore 管理状态
 */

import Modal from '../common/Modal';
import LoginForm from './LoginForm';
import RegisterForm from './RegisterForm';
import { useAuthModalStore } from '../../stores/useAuthModalStore';

export default function AuthModal() {
  const { isOpen, mode, close, switchMode } = useAuthModalStore();

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
