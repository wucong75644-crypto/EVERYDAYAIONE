/**
 * 删除消息确认弹框
 *
 * 改造（V2 - 设计系统重构）：
 * - 使用 common/Modal 组件，统一动画/边距/关闭逻辑
 * - 移除手写的 closing 状态、ESC、背景滚动逻辑
 * - 颜色全部使用 token，跟随主题切换
 * - 移除内联 SVG 改用 lucide
 */

import { AlertTriangle } from 'lucide-react';
import Modal from '../../common/Modal';
import { Button } from '../../ui/Button';

interface DeleteMessageModalProps {
  isOpen: boolean;
  onClose: () => void;
  onConfirm: () => Promise<void>;
  loading?: boolean;
}

export default function DeleteMessageModal({
  isOpen,
  onClose,
  onConfirm,
  loading = false,
}: DeleteMessageModalProps) {
  return (
    <Modal
      isOpen={isOpen}
      onClose={loading ? () => {} : onClose}
      showCloseButton={false}
      maxWidth="max-w-sm"
      closeOnOverlay={!loading}
      closeOnEsc={!loading}
    >
      <div className="flex items-start gap-3">
        <div className="w-10 h-10 bg-warning-light rounded-full flex items-center justify-center flex-shrink-0">
          <AlertTriangle className="w-5 h-5 text-warning" />
        </div>
        <div className="flex-1">
          <h3 className="text-lg font-medium text-text-primary">确定删除这条消息？</h3>
          <p className="mt-2 text-sm text-text-tertiary">删除后不可恢复。</p>
        </div>
      </div>

      <div className="mt-6 flex gap-3 justify-end">
        <Button variant="secondary" size="md" onClick={onClose} disabled={loading}>
          取消
        </Button>
        <Button
          size="md"
          loading={loading}
          onClick={onConfirm}
          className="bg-error text-text-on-accent hover:bg-error/90"
        >
          删除
        </Button>
      </div>
    </Modal>
  );
}
