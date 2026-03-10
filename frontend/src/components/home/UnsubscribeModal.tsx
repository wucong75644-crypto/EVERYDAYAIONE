/**
 * 取消订阅确认弹窗
 *
 * 使用通用 Modal 组件，确认后执行取消订阅操作。
 */

import { Loader2 } from 'lucide-react';
import Modal from '../common/Modal';

interface UnsubscribeModalProps {
  isOpen: boolean;
  modelName: string;
  isLoading: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}

export default function UnsubscribeModal({
  isOpen,
  modelName,
  isLoading,
  onConfirm,
  onCancel,
}: UnsubscribeModalProps) {
  return (
    <Modal isOpen={isOpen} onClose={onCancel} title="确认取消订阅？" maxWidth="max-w-sm">
      <p className="text-sm text-gray-600">
        取消订阅 <span className="font-medium text-gray-900">{modelName}</span> 后，将无法在聊天页使用该模型。
      </p>
      <div className="flex gap-3 mt-6">
        <button
          onClick={onCancel}
          disabled={isLoading}
          className="flex-1 border border-gray-300 text-gray-700 py-2 rounded-lg text-sm font-medium hover:bg-gray-50 transition-colors disabled:opacity-50"
        >
          保持订阅
        </button>
        <button
          onClick={onConfirm}
          disabled={isLoading}
          className="flex-1 bg-red-600 text-white py-2 rounded-lg text-sm font-medium hover:bg-red-700 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
        >
          {isLoading ? (
            <span className="inline-flex items-center justify-center gap-1.5">
              <Loader2 className="w-4 h-4 animate-spin" />
              取消中...
            </span>
          ) : (
            '确认取消'
          )}
        </button>
      </div>
    </Modal>
  );
}
