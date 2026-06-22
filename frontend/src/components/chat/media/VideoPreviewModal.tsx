/**
 * 视频全屏预览弹窗
 *
 * 仿 ImagePreviewModal 架构（Portal + 黑色遮罩 + ESC/箭头键盘交互），
 * 内容简化为浏览器原生 <video controls>，不做缩放/拖拽。
 */

import { useEffect, useCallback, useState, memo } from 'react';
import { createPortal } from 'react-dom';
import { X, Download, ChevronLeft, ChevronRight, Loader2 } from 'lucide-react';
import { downloadFile } from '../../../utils/downloadFile';

interface VideoPreviewModalProps {
  videoUrl: string | null;
  onClose: () => void;
  filename?: string;
  onPrev?: () => void;
  onNext?: () => void;
  hasPrev?: boolean;
  hasNext?: boolean;
}

export default memo(function VideoPreviewModal({
  videoUrl,
  onClose,
  filename = 'video',
  onPrev,
  onNext,
  hasPrev = false,
  hasNext = false,
}: VideoPreviewModalProps) {
  const [isClosing, setIsClosing] = useState(false);
  const [isDownloading, setIsDownloading] = useState(false);
  const [loadError, setLoadError] = useState(false);

  // 关闭动画
  const handleClose = useCallback(() => {
    setIsClosing(true);
    setTimeout(() => {
      setIsClosing(false);
      onClose();
    }, 150);
  }, [onClose]);

  // 切视频时重置错误态
  useEffect(() => {
    setLoadError(false);
  }, [videoUrl]);

  // 键盘：ESC 关闭、左右箭头切上下张
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') handleClose();
      else if (e.key === 'ArrowLeft' && hasPrev && onPrev) onPrev();
      else if (e.key === 'ArrowRight' && hasNext && onNext) onNext();
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [handleClose, hasPrev, hasNext, onPrev, onNext]);

  // 打开时禁止背景滚动
  useEffect(() => {
    document.body.style.overflow = 'hidden';
    return () => { document.body.style.overflow = ''; };
  }, []);

  const handleDownload = useCallback(async () => {
    if (!videoUrl || isDownloading) return;
    try {
      setIsDownloading(true);
      await downloadFile(videoUrl, filename);
    } finally {
      setIsDownloading(false);
    }
  }, [videoUrl, filename, isDownloading]);

  if (!videoUrl) return null;

  return createPortal(
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/90"
      onClick={(e) => { if (e.target === e.currentTarget) handleClose(); }}
      style={{
        animation: isClosing
          ? 'preview-backdrop-exit 150ms ease-out forwards'
          : 'preview-backdrop 200ms ease-out forwards',
      }}
    >
      {/* 顶部工具栏 */}
      <div className="absolute top-0 left-0 right-0 flex items-center justify-between px-6 py-3 bg-gradient-to-b from-black/70 to-transparent z-10">
        <div className="text-white text-sm font-medium truncate max-w-[60%]">{filename}</div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={handleDownload}
            disabled={isDownloading}
            className="p-2 rounded-lg hover:bg-white/10 text-white transition-colors disabled:opacity-50"
            title="下载"
            aria-label="下载视频"
          >
            {isDownloading ? <Loader2 className="w-5 h-5 animate-spin" /> : <Download className="w-5 h-5" />}
          </button>
          <button
            type="button"
            onClick={handleClose}
            className="p-2 rounded-lg hover:bg-white/10 text-white transition-colors"
            title="关闭"
            aria-label="关闭预览"
          >
            <X className="w-5 h-5" />
          </button>
        </div>
      </div>

      {/* 左右切换箭头 */}
      {hasPrev && onPrev && (
        <button
          type="button"
          onClick={onPrev}
          className="absolute left-4 top-1/2 -translate-y-1/2 p-3 rounded-full bg-black/40 hover:bg-black/60 text-white z-10 transition-colors"
          aria-label="上一个视频"
        >
          <ChevronLeft className="w-6 h-6" />
        </button>
      )}
      {hasNext && onNext && (
        <button
          type="button"
          onClick={onNext}
          className="absolute right-4 top-1/2 -translate-y-1/2 p-3 rounded-full bg-black/40 hover:bg-black/60 text-white z-10 transition-colors"
          aria-label="下一个视频"
        >
          <ChevronRight className="w-6 h-6" />
        </button>
      )}

      {/* 视频内容（或加载失败回退） */}
      {loadError ? (
        <div className="text-white text-center px-6">
          <div className="text-4xl mb-3" aria-hidden>🎬</div>
          <div className="mb-4">视频加载失败</div>
          <button
            type="button"
            onClick={handleDownload}
            className="px-4 py-2 rounded-lg bg-white/10 hover:bg-white/20 text-sm transition-colors"
          >
            点击下载
          </button>
        </div>
      ) : (
        <video
          key={videoUrl}
          src={videoUrl}
          controls
          autoPlay
          preload="metadata"
          onError={() => setLoadError(true)}
          onClick={(e) => e.stopPropagation()}
          className="max-w-[90vw] max-h-[85vh] rounded-lg shadow-2xl bg-black"
          style={{
            animation: 'preview-content 200ms ease-out forwards',
          }}
        >
          您的浏览器不支持视频播放
        </video>
      )}
    </div>,
    document.body,
  );
});
