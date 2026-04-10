/**
 * 上传错误提示条组件
 *
 * 显示图片/音频上传错误信息，支持关闭操作
 */

import { AlertCircle, X } from 'lucide-react';

interface UploadErrorBarProps {
  error: string | null;
  onDismiss: () => void;
}

export default function UploadErrorBar({ error, onDismiss }: UploadErrorBarProps) {
  if (!error) return null;

  return (
    <div className="mb-2 px-3 py-2 bg-error-light border border-error/20 rounded-lg flex items-start space-x-2 transition-all duration-300 ease-out overflow-hidden">
      <AlertCircle className="w-4 h-4 text-error flex-shrink-0 mt-0.5" />
      <div className="flex-1 text-xs text-error">{error}</div>
      <button
        onClick={onDismiss}
        className="flex-shrink-0 text-error hover:text-error/80 transition-base"
        aria-label="关闭错误提示"
      >
        <X className="w-4 h-4" />
      </button>
    </div>
  );
}
