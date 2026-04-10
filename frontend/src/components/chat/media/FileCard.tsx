/**
 * 文件下载卡片组件
 *
 * 在消息中展示可下载/可预览的文件。
 */

import { useState } from 'react';
import toast from 'react-hot-toast';
import type { FilePart } from '../../../types/message';
import { downloadFile } from '../../../utils/downloadFile';
import { getFileIcon, formatFileSize } from '../../../utils/fileUtils';
import FilePreviewModal, { canPreview } from './FilePreviewModal';

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
      className={`flex items-center gap-3 px-4 py-3 rounded-xl bg-surface dark:bg-surface-dark-card hover:bg-hover dark:hover:bg-surface-dark-card transition-base border border-border-default dark:border-border-dark ${onPreview ? 'cursor-pointer' : ''}`}
      onClick={onPreview}
    >
      <span className="text-2xl flex-shrink-0">{getFileIcon(file.name)}</span>
      <div className="flex-1 min-w-0">
        <div className="text-sm font-medium text-text-primary dark:text-text-primary truncate">
          {file.name}
        </div>
        {file.size && (
          <div className="text-xs text-text-tertiary dark:text-text-disabled">
            {formatFileSize(file.size)}
          </div>
        )}
      </div>
      {onPreview && (
        <span className="text-xs text-accent flex-shrink-0">预览</span>
      )}
      <button
        onClick={handleDownload}
        className="p-1.5 rounded-lg hover:bg-active dark:hover:bg-active transition-base flex-shrink-0"
        title="下载"
      >
        <svg className="w-4 h-4 text-text-tertiary" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4" />
        </svg>
      </button>
    </div>
  );
}
