/**
 * PDF 文件预览组件
 *
 * 显示上传的 PDF 文件卡片，支持删除和状态显示
 */

import { type UploadedFile } from '../../hooks/useFileUpload';

interface FilePreviewProps {
  files: UploadedFile[];
  onRemove: (fileId: string) => void;
}

function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes}B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)}KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)}MB`;
}

export default function FilePreview({ files, onRemove }: FilePreviewProps) {
  if (files.length === 0) return null;

  return (
    <div className="mb-2 flex flex-wrap gap-2">
      {files.map((file) => (
        <div
          key={file.id}
          className={`relative flex items-center gap-2 rounded-lg border px-3 py-2 text-sm ${
            file.error
              ? 'border-red-300 bg-red-50 dark:border-red-700 dark:bg-red-900/20'
              : 'border-gray-200 bg-gray-50 dark:border-gray-700 dark:bg-gray-800'
          }`}
        >
          {/* PDF 图标 */}
          <svg className="h-5 w-5 shrink-0 text-red-500" viewBox="0 0 24 24" fill="currentColor">
            <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8l-6-6zm-1 1.5L18.5 9H13V3.5zM6 20V4h5v7h7v9H6z" />
            <text x="7" y="18" fontSize="6" fontWeight="bold" fill="currentColor">PDF</text>
          </svg>

          {/* 文件信息 */}
          <div className="min-w-0 flex-1">
            <div className="truncate max-w-[160px] font-medium text-gray-700 dark:text-gray-300">
              {file.name}
            </div>
            <div className="text-xs text-gray-400">
              {file.error || formatFileSize(file.size)}
            </div>
          </div>

          {/* 上传中 spinner */}
          {file.isUploading && (
            <div className="h-4 w-4 shrink-0 animate-spin rounded-full border-2 border-gray-300 border-t-blue-500" />
          )}

          {/* 删除按钮 */}
          <button
            onClick={() => onRemove(file.id)}
            disabled={file.isUploading}
            className="shrink-0 rounded p-0.5 text-gray-400 hover:text-gray-600 disabled:cursor-not-allowed disabled:opacity-50 dark:hover:text-gray-300"
            title="删除文件"
          >
            <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>
      ))}
    </div>
  );
}
