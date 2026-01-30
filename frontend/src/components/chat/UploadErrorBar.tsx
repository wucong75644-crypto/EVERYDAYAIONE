/**
 * 上传错误提示条组件
 *
 * 显示图片/音频上传错误信息，支持关闭操作
 */

interface UploadErrorBarProps {
  error: string | null;
  onDismiss: () => void;
}

export default function UploadErrorBar({ error, onDismiss }: UploadErrorBarProps) {
  if (!error) return null;

  return (
    <div className="mb-2 px-3 py-2 bg-red-50 border border-red-200 rounded-lg flex items-start space-x-2 transition-all duration-300 ease-out overflow-hidden">
      <svg className="w-4 h-4 text-red-600 flex-shrink-0 mt-0.5" fill="currentColor" viewBox="0 0 20 20">
        <path
          fillRule="evenodd"
          d="M10 18a8 8 0 100-16 8 8 0 000 16zM8.707 7.293a1 1 0 00-1.414 1.414L8.586 10l-1.293 1.293a1 1 0 101.414 1.414L10 11.414l1.293 1.293a1 1 0 001.414-1.414L11.414 10l1.293-1.293a1 1 0 00-1.414-1.414L10 8.586 8.707 7.293z"
          clipRule="evenodd"
        />
      </svg>
      <div className="flex-1 text-xs text-red-800">{error}</div>
      <button
        onClick={onDismiss}
        className="flex-shrink-0 text-red-600 hover:text-red-800"
        aria-label="关闭错误提示"
      >
        <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
        </svg>
      </button>
    </div>
  );
}
