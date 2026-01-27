/**
 * 图片预览弹窗组件
 *
 * 替代 window.open 的应用内图片预览，支持：
 * - 全屏预览
 * - 缩放操作（滚轮/双击）
 * - 下载功能（fetch + blob 解决跨域问题）
 * - 键盘交互（ESC 关闭）
 */

import { useState, useEffect, useCallback, useRef, memo } from 'react';
import { X, Download, ZoomIn, ZoomOut, RotateCcw, Loader2 } from 'lucide-react';

interface ImagePreviewModalProps {
  /** 图片 URL */
  imageUrl: string | null;
  /** 关闭回调 */
  onClose: () => void;
  /** 文件名（用于下载） */
  filename?: string;
}

export default memo(function ImagePreviewModal({
  imageUrl,
  onClose,
  filename = 'image',
}: ImagePreviewModalProps) {
  // 缩放状态
  const [scale, setScale] = useState(1);
  const [position, setPosition] = useState({ x: 0, y: 0 });
  const [isDragging, setIsDragging] = useState(false);
  const [dragStart, setDragStart] = useState({ x: 0, y: 0 });
  const [isDownloading, setIsDownloading] = useState(false);
  const imageRef = useRef<HTMLImageElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);

  // 缩放限制
  const MIN_SCALE = 0.5;
  const MAX_SCALE = 4;
  const SCALE_STEP = 0.25;

  // 重置视图
  const resetView = useCallback(() => {
    setScale(1);
    setPosition({ x: 0, y: 0 });
  }, []);

  // 缩放操作
  const handleZoomIn = useCallback(() => {
    setScale((s) => Math.min(s + SCALE_STEP, MAX_SCALE));
  }, []);

  const handleZoomOut = useCallback(() => {
    setScale((s) => Math.max(s - SCALE_STEP, MIN_SCALE));
  }, []);

  // 滚轮缩放
  const handleWheel = useCallback((e: React.WheelEvent) => {
    e.preventDefault();
    const delta = e.deltaY > 0 ? -SCALE_STEP : SCALE_STEP;
    setScale((s) => Math.min(Math.max(s + delta, MIN_SCALE), MAX_SCALE));
  }, []);

  // 双击缩放
  const handleDoubleClick = useCallback(() => {
    if (scale === 1) {
      setScale(2);
    } else {
      resetView();
    }
  }, [scale, resetView]);

  // 拖拽开始
  const handleMouseDown = useCallback((e: React.MouseEvent) => {
    if (scale > 1) {
      setIsDragging(true);
      setDragStart({ x: e.clientX - position.x, y: e.clientY - position.y });
    }
  }, [scale, position]);

  // 拖拽移动
  const handleMouseMove = useCallback((e: React.MouseEvent) => {
    if (isDragging) {
      setPosition({
        x: e.clientX - dragStart.x,
        y: e.clientY - dragStart.y,
      });
    }
  }, [isDragging, dragStart]);

  // 拖拽结束
  const handleMouseUp = useCallback(() => {
    setIsDragging(false);
  }, []);

  // 下载图片（使用 fetch + blob 解决跨域问题）
  const handleDownload = useCallback(async () => {
    if (!imageUrl || isDownloading) return;

    try {
      setIsDownloading(true);

      // 使用 fetch 获取图片数据
      const response = await fetch(imageUrl);
      if (!response.ok) {
        throw new Error('下载失败');
      }

      const blob = await response.blob();
      const blobUrl = URL.createObjectURL(blob);

      // 创建下载链接
      const link = document.createElement('a');
      link.href = blobUrl;
      link.download = `${filename}.${getExtensionFromBlob(blob)}`;
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);

      // 释放 blob URL
      URL.revokeObjectURL(blobUrl);
    } catch (error) {
      console.error('下载图片失败:', error);
      // 降级：尝试直接打开链接
      window.open(imageUrl, '_blank');
    } finally {
      setIsDownloading(false);
    }
  }, [imageUrl, filename, isDownloading]);

  // ESC 关闭
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        onClose();
      }
    };

    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [onClose]);

  // 打开时禁止背景滚动
  useEffect(() => {
    document.body.style.overflow = 'hidden';
    return () => {
      document.body.style.overflow = '';
    };
  }, []);

  // 重置视图当图片改变
  useEffect(() => {
    resetView();
  }, [imageUrl, resetView]);

  if (!imageUrl) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/90"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      {/* 顶部工具栏 */}
      <div className="absolute top-0 left-0 right-0 flex items-center justify-between px-4 py-3 bg-gradient-to-b from-black/50 to-transparent">
        <div className="flex items-center gap-2">
          {/* 缩放比例显示 */}
          <span className="text-white/80 text-sm tabular-nums">
            {Math.round(scale * 100)}%
          </span>
        </div>

        <div className="flex items-center gap-1">
          {/* 缩小 */}
          <button
            onClick={handleZoomOut}
            disabled={scale <= MIN_SCALE}
            className="p-2 text-white/80 hover:text-white hover:bg-white/10 rounded-lg transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
            title="缩小"
          >
            <ZoomOut className="w-5 h-5" />
          </button>

          {/* 放大 */}
          <button
            onClick={handleZoomIn}
            disabled={scale >= MAX_SCALE}
            className="p-2 text-white/80 hover:text-white hover:bg-white/10 rounded-lg transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
            title="放大"
          >
            <ZoomIn className="w-5 h-5" />
          </button>

          {/* 重置 */}
          <button
            onClick={resetView}
            className="p-2 text-white/80 hover:text-white hover:bg-white/10 rounded-lg transition-colors"
            title="重置视图"
          >
            <RotateCcw className="w-5 h-5" />
          </button>

          {/* 分隔线 */}
          <div className="w-px h-5 bg-white/20 mx-1" />

          {/* 下载 */}
          <button
            onClick={handleDownload}
            disabled={isDownloading}
            className="p-2 text-white/80 hover:text-white hover:bg-white/10 rounded-lg transition-colors disabled:opacity-60"
            title="下载"
          >
            {isDownloading ? (
              <Loader2 className="w-5 h-5 animate-spin" />
            ) : (
              <Download className="w-5 h-5" />
            )}
          </button>

          {/* 关闭 */}
          <button
            onClick={onClose}
            className="p-2 text-white/80 hover:text-white hover:bg-white/10 rounded-lg transition-colors"
            title="关闭 (ESC)"
          >
            <X className="w-5 h-5" />
          </button>
        </div>
      </div>

      {/* 图片容器 */}
      <div
        ref={containerRef}
        className={`w-full h-full flex items-center justify-center overflow-hidden ${
          isDragging ? 'cursor-grabbing' : scale > 1 ? 'cursor-grab' : 'cursor-zoom-in'
        }`}
        onWheel={handleWheel}
        onMouseDown={handleMouseDown}
        onMouseMove={handleMouseMove}
        onMouseUp={handleMouseUp}
        onMouseLeave={handleMouseUp}
        onDoubleClick={handleDoubleClick}
      >
        <img
          ref={imageRef}
          src={imageUrl}
          alt="预览图片"
          className="max-w-[90vw] max-h-[90vh] object-contain select-none"
          style={{
            transform: `translate(${position.x}px, ${position.y}px) scale(${scale})`,
            transition: isDragging ? 'none' : 'transform 0.2s ease-out',
          }}
          draggable={false}
        />
      </div>

      {/* 底部提示 */}
      <div className="absolute bottom-4 left-1/2 -translate-x-1/2 text-white/60 text-xs">
        滚轮缩放 · 双击重置 · ESC 关闭
      </div>
    </div>
  );
});

/**
 * 从 Blob MIME 类型获取文件扩展名
 */
function getExtensionFromBlob(blob: Blob): string {
  const mimeType = blob.type;
  const mimeMap: Record<string, string> = {
    'image/png': 'png',
    'image/jpeg': 'jpg',
    'image/gif': 'gif',
    'image/webp': 'webp',
    'image/svg+xml': 'svg',
  };
  return mimeMap[mimeType] || 'png';
}
