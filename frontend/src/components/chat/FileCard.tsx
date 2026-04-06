/**
 * 文件下载卡片组件
 *
 * 在消息中展示可下载/可预览的文件。
 */

import { useState } from 'react';
import toast from 'react-hot-toast';
import type { FilePart } from '../../types/message';
import { downloadFile } from '../../utils/downloadFile';
import FilePreviewModal, { canPreview } from './FilePreviewModal';

/** 文件类型图标映射 */
function getFileIcon(name: string): string {
  const ext = name.split('.').pop()?.toLowerCase() || '';
  if (['xlsx', 'xls', 'csv', 'tsv'].includes(ext)) return '\uD83D\uDCCA';
  if (ext === 'pdf') return '\uD83D\uDCC4';
  if (['doc', 'docx', 'txt', 'md'].includes(ext)) return '\uD83D\uDCC3';
  if (['zip', 'rar', '7z'].includes(ext)) return '\uD83D\uDCE6';
  return '\uD83D\uDCCE';
}

/** 格式化文件大小 */
function formatFileSize(bytes?: number): string {
  if (!bytes) return '';
  if (bytes < 1024) return `${bytes}B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)}KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)}MB`;
}

/** 文件卡片列表（含预览弹窗状态） */
export default function FileCardList({ files }: { files: FilePart[] }) {
  const [previewFile, setPreviewFile] = useState<FilePart | null>(null);

  return (
    <div className="mt-3 space-y-2">
      {files.map((file) => (
        <FileCardItem
          key={file.url}
          file={file}
          onPreview={canPreview(file.name) ? () => setPreviewFile(file) : undefined}
        />
      ))}
      {previewFile && (
        <FilePreviewModal
          file={previewFile}
          onClose={() => setPreviewFile(null)}
        />
      )}
    </div>
  );
}

/** 单个文件卡片 */
function FileCardItem({ file, onPreview }: { file: FilePart; onPreview?: () => void }) {
  const handleDownload = async (e: React.MouseEvent) => {
    e.stopPropagation();
    try {
      await downloadFile(file.url, file.name);
    } catch {
      toast.error('下载失败，请重试');
    }
  };

  return (
    <div
      className={`flex items-center gap-3 px-4 py-3 rounded-xl bg-gray-50 dark:bg-gray-800 hover:bg-gray-100 dark:hover:bg-gray-700 transition-colors border border-gray-200 dark:border-gray-700 ${onPreview ? 'cursor-pointer' : ''}`}
      onClick={onPreview}
    >
      <span className="text-2xl flex-shrink-0">{getFileIcon(file.name)}</span>
      <div className="flex-1 min-w-0">
        <div className="text-sm font-medium text-gray-900 dark:text-gray-100 truncate">
          {file.name}
        </div>
        {file.size && (
          <div className="text-xs text-gray-500 dark:text-gray-400">
            {formatFileSize(file.size)}
          </div>
        )}
      </div>
      {onPreview && (
        <span className="text-xs text-blue-500 flex-shrink-0">预览</span>
      )}
      <button
        onClick={handleDownload}
        className="p-1.5 rounded-lg hover:bg-gray-200 dark:hover:bg-gray-600 transition-colors flex-shrink-0"
        title="下载"
      >
        <svg className="w-4 h-4 text-gray-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4" />
        </svg>
      </button>
    </div>
  );
}
