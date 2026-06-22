/**
 * PreviewFrame — 全屏 shell（遮罩 + 顶部工具栏 + loading + error），
 * 共享给所有「文档类」adapter：Pdf / Spreadsheet / Text / Docx / Pptx / Fallback。
 *
 * Image / Video adapter 不用这个 Frame（它们的 Modal 有自己的 UI），
 * 直接复用底层 ImagePreviewModal / VideoPreviewModal。
 *
 * 此组件 1:1 复刻原 FilePreviewModal.tsx:252-298 的 shell 样式，
 * 确保用户感知零差异。
 */

import { type ReactNode, useEffect } from 'react';
import { createPortal } from 'react-dom';
import { Download, Loader2, X } from 'lucide-react';
import toast from 'react-hot-toast';
import { downloadFile } from '../utils/downloadFile';
import { getFileIcon, formatFileSize } from '../utils/fileUtils';
import { resolvePreviewUrl } from './fetchPreview';
import type { PreviewItem } from './types';

interface PreviewFrameProps {
  item: PreviewItem;
  onClose: () => void;
  /** 加载中显示 spinner（覆盖 children） */
  loading?: boolean;
  /** 自定义 loading 文案（默认仅显示 spinner，不显示文字）*/
  loadingText?: string;
  /** 错误文案，非空时显示红色错误（覆盖 children） */
  error?: string | null;
  /** 自定义下载行为；默认走 downloadFile(CDN URL or 后端代理) */
  onDownload?: () => void | Promise<void>;
  /** 实际内容（PDF iframe / 表格 / 文本 / docx HTML 等）*/
  children?: ReactNode;
  /** 底部工具栏（如 Excel 多 Sheet tab）*/
  footer?: ReactNode;
}

export default function PreviewFrame({
  item,
  onClose,
  loading = false,
  loadingText,
  error = null,
  onDownload,
  children,
  footer,
}: PreviewFrameProps) {
  // ESC 关闭
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  // 打开时禁止背景滚动
  useEffect(() => {
    document.body.style.overflow = 'hidden';
    return () => {
      document.body.style.overflow = '';
    };
  }, []);

  const handleDownload = async () => {
    if (onDownload) {
      await onDownload();
      return;
    }
    // 默认行为：复用 downloadFile 工具
    const url = resolvePreviewUrl(item);
    if (!url) {
      toast.error('下载失败：无可用 URL');
      return;
    }
    try {
      await downloadFile(url, item.filename);
    } catch {
      toast.error('下载失败，请重试');
    }
  };

  return createPortal(
    <div className="fixed inset-0 z-50 flex flex-col bg-black/80">
      {/* 点击遮罩关闭（独立层，不影响内容区点击）*/}
      <div className="absolute inset-0 -z-10" onClick={onClose} />

      {/* 顶部工具栏 — 1:1 复刻原 FilePreviewModal */}
      <div className="flex items-center justify-between px-6 py-3 bg-gray-900/90 text-white flex-shrink-0">
        <div className="flex items-center gap-3 min-w-0">
          <span className="text-lg">{getFileIcon(item.filename)}</span>
          <span className="truncate font-medium">{item.filename}</span>
          {item.size != null && (
            <span className="text-sm text-gray-400 flex-shrink-0">
              {formatFileSize(item.size)}
            </span>
          )}
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={handleDownload}
            className="p-2 rounded-lg hover:bg-white/10 transition-colors"
            title="下载"
            aria-label="下载"
          >
            <Download size={20} />
          </button>
          <button
            onClick={onClose}
            className="p-2 rounded-lg hover:bg-white/10 transition-colors"
            title="关闭"
            aria-label="关闭"
          >
            <X size={20} />
          </button>
        </div>
      </div>

      {/* 内容区域 */}
      <div className="flex-1 overflow-auto min-h-0">
        {loading && (
          <div className="flex flex-col items-center justify-center h-full gap-3">
            <Loader2 className="w-8 h-8 animate-spin text-white" />
            {loadingText && <div className="text-sm text-gray-300">{loadingText}</div>}
          </div>
        )}

        {!loading && error && (
          <div className="flex items-center justify-center h-full text-red-400 px-6 text-center">
            {error}
          </div>
        )}

        {!loading && !error && children}
      </div>

      {/* 底部工具栏（可选，Excel Sheet tab 等） */}
      {footer}
    </div>,
    document.body,
  );
}
