/**
 * 删除消息确认弹框
 */

import { useEffect } from 'react';
import { AlertTriangle, X } from 'lucide-react';

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
  // ESC键关闭弹框
  useEffect(() => {
    if (!isOpen) return;

    const handleEscape = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && !loading) {
        onClose();
      }
    };

    document.addEventListener('keydown', handleEscape);
    return () => document.removeEventListener('keydown', handleEscape);
  }, [isOpen, loading, onClose]);

  // 阻止背景滚动
  useEffect(() => {
    if (isOpen) {
      document.body.style.overflow = 'hidden';
    } else {
      document.body.style.overflow = '';
    }
    return () => {
      document.body.style.overflow = '';
    };
  }, [isOpen]);

  if (!isOpen) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 animate-in fade-in duration-150"
      onClick={(e) => {
        if (e.target === e.currentTarget && !loading) {
          onClose();
        }
      }}
    >
      {/* Modal 本体：参考 shadcn/ui 的动画 - scale 从 95% 到 100% + 淡入 */}
      <div
        className="bg-white rounded-xl shadow-xl w-full max-w-md mx-4 relative"
        style={{
          animation: 'modal-enter 200ms cubic-bezier(0.32, 0.72, 0, 1)',
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
        `}</style>
        {/* 关闭按钮 */}
        <button
          onClick={onClose}
          disabled={loading}
          className="absolute top-4 right-4 p-1 text-gray-400 hover:text-gray-600 rounded-lg transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
          aria-label="关闭"
        >
          <X className="w-5 h-5" />
        </button>

        {/* 内容 */}
        <div className="p-6">
          {/* 警告图标 */}
          <div className="flex items-start gap-3 mb-4">
            <div className="w-10 h-10 bg-orange-100 rounded-full flex items-center justify-center flex-shrink-0">
              <AlertTriangle className="w-5 h-5 text-orange-500" />
            </div>
            <div className="flex-1">
              <h3 className="text-lg font-medium text-gray-900">确定删除这条消息？</h3>
            </div>
          </div>

          {/* 说明文字 */}
          <p className="text-sm text-gray-500 ml-13">删除后不可恢复。</p>

          {/* 按钮组 */}
          <div className="flex gap-3 justify-end mt-6">
            <button
              onClick={onClose}
              disabled={loading}
              className="px-4 py-2 text-gray-700 bg-gray-100 hover:bg-gray-200 rounded-lg transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
            >
              取消
            </button>
            <button
              onClick={onConfirm}
              disabled={loading}
              className="px-4 py-2 text-white bg-red-500 hover:bg-red-600 rounded-lg transition-colors disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-2"
            >
              {loading && (
                <svg
                  className="animate-spin h-4 w-4 text-white"
                  xmlns="http://www.w3.org/2000/svg"
                  fill="none"
                  viewBox="0 0 24 24"
                >
                  <circle
                    className="opacity-25"
                    cx="12"
                    cy="12"
                    r="10"
                    stroke="currentColor"
                    strokeWidth="4"
                  ></circle>
                  <path
                    className="opacity-75"
                    fill="currentColor"
                    d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"
                  ></path>
                </svg>
              )}
              删除
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
