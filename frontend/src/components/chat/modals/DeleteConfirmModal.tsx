/**
 * 删除对话确认弹框
 *
 * 改造（V2 - 设计系统重构）：
 * - 改为受控模式（接收 isOpen 而非 closing）
 * - 内部使用 common/Modal 组件，统一动画/边距/关闭逻辑
 * - 颜色全部使用 token，跟随主题切换
 * - 删除手写的 ESC/背景滚动逻辑（Modal 已处理）
 */

import { AlertTriangle } from 'lucide-react';
import Modal from '../../common/Modal';
import { Button } from '../../ui/Button';

interface DeleteConfirmModalProps {
  isOpen: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}

export default function DeleteConfirmModal({
  isOpen,
  onConfirm,
  onCancel,
}: DeleteConfirmModalProps) {
  return (
    <Modal isOpen={isOpen} onClose={onCancel} showCloseButton={false} maxWidth="max-w-sm">
      <div className="flex items-start gap-3">
        <div className="w-10 h-10 bg-warning-light rounded-full flex items-center justify-center flex-shrink-0">
          <AlertTriangle className="w-5 h-5 text-warning" />
        </div>
        <div className="flex-1">
          <h3 className="text-lg font-medium text-text-primary">确定删除对话？</h3>
          <p className="mt-2 text-sm text-text-tertiary">删除后，聊天记录将不可恢复。</p>
        </div>
      </div>

      <div className="mt-6 flex gap-3 justify-end">
        <Button variant="secondary" size="md" onClick={onCancel}>
          取消
        </Button>
        <Button
          size="md"
          onClick={onConfirm}
          className="bg-error text-text-on-accent hover:bg-error/90"
        >
          删除
        </Button>
      </div>
    </Modal>
  );
}
