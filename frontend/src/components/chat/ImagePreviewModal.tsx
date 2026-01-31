/**
 * 图片预览弹窗组件
 *
 * 替代 window.open 的应用内图片预览，支持：
 * - 全屏预览
 * - 缩放操作（滚轮/双击）
 * - 下载功能（fetch + blob）
 * - 键盘交互（ESC 关闭）
 */

import { useState, useEffect, useCallback, useRef, memo } from 'react';
import { X, Download, ZoomIn, ZoomOut, RotateCcw, Loader2, Trash2, ChevronLeft, ChevronRight } from 'lucide-react';

interface ImagePreviewModalProps {
  /** 图片 URL */
  imageUrl: string | null;
  /** 关闭回调 */
  onClose: () => void;
  /** 文件名（用于下载） */
  filename?: string;
  /** 删除回调（可选，用于输入框图片预览） */
  onDelete?: () => void;
  /** 上一张回调 */
  onPrev?: () => void;
  /** 下一张回调 */
  onNext?: () => void;
  /** 是否有上一张 */
  hasPrev?: boolean;
  /** 是否有下一张 */
  hasNext?: boolean;
  /** 所有图片列表（用于底部缩略图预览） */
  allImages?: string[];
  /** 当前图片索引（用于底部缩略图预览） */
  currentIndex?: number;
  /** 选择图片回调（用于底部缩略图预览） */
  onSelectImage?: (index: number) => void;
}

export default memo(function ImagePreviewModal({
  imageUrl,
  onClose,
  filename = 'image',
  onDelete,
  onPrev,
  onNext,
  hasPrev = false,
  hasNext = false,
  allImages = [],
  currentIndex = 0,
  onSelectImage,
}: ImagePreviewModalProps) {
  // 缩放状态
  const [scale, setScale] = useState(1);
  const [position, setPosition] = useState({ x: 0, y: 0 });
  const [isDragging, setIsDragging] = useState(false);
  const [dragStart, setDragStart] = useState({ x: 0, y: 0 });
  const [isDownloading, setIsDownloading] = useState(false);
  const [isClosing, setIsClosing] = useState(false);
  const imageRef = useRef<HTMLImageElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const thumbnailContainerRef = useRef<HTMLDivElement>(null);
  const activeThumbnailRef = useRef<HTMLButtonElement>(null);

  // 处理关闭动画
  const handleClose = useCallback(() => {
    setIsClosing(true);
    setTimeout(() => {
      setIsClosing(false);
      onClose();
    }, 150);
  }, [onClose]);

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

  // 滚轮缩放（使用原生事件监听器以支持 preventDefault）
  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    const handleWheel = (e: WheelEvent) => {
      e.preventDefault();
      const delta = e.deltaY > 0 ? -SCALE_STEP : SCALE_STEP;
      setScale((s) => Math.min(Math.max(s + delta, MIN_SCALE), MAX_SCALE));
    };

    // 添加原生事件监听器，设置 passive: false 以允许 preventDefault
    container.addEventListener('wheel', handleWheel, { passive: false });

    return () => {
      container.removeEventListener('wheel', handleWheel);
    };
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

  // 下载图片（使用 fetch + blob）
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
    } catch {
      // 下载失败时降级：直接打开链接让用户手动下载
      window.open(imageUrl, '_blank');
    } finally {
      setIsDownloading(false);
    }
  }, [imageUrl, filename, isDownloading]);

  // 键盘快捷键：ESC 关闭、左右箭头切换、Delete 删除
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        handleClose();
      } else if (e.key === 'ArrowLeft' && hasPrev && onPrev) {
        onPrev();
      } else if (e.key === 'ArrowRight' && hasNext && onNext) {
        onNext();
      } else if ((e.key === 'Delete' || e.key === 'Backspace') && onDelete) {
        onDelete();
      }
    };

    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [handleClose, hasPrev, hasNext, onPrev, onNext, onDelete]);

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

  // 自动滚动缩略图到当前选中项
  useEffect(() => {
    if (activeThumbnailRef.current && thumbnailContainerRef.current) {
      const thumbnail = activeThumbnailRef.current;
      const container = thumbnailContainerRef.current;

      // 计算缩略图相对于容器的位置
      const thumbnailLeft = thumbnail.offsetLeft;
      const thumbnailWidth = thumbnail.offsetWidth;
      const containerScrollLeft = container.scrollLeft;
      const containerWidth = container.offsetWidth;

      // 如果缩略图在可视区域左侧外
      if (thumbnailLeft < containerScrollLeft) {
        container.scrollTo({
          left: thumbnailLeft - 8, // 留 8px 边距
          behavior: 'smooth',
        });
      }
      // 如果缩略图在可视区域右侧外
      else if (thumbnailLeft + thumbnailWidth > containerScrollLeft + containerWidth) {
        container.scrollTo({
          left: thumbnailLeft + thumbnailWidth - containerWidth + 8, // 留 8px 边距
          behavior: 'smooth',
        });
      }
    }
  }, [currentIndex]);

  if (!imageUrl) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center"
      onClick={(e) => {
        if (e.target === e.currentTarget) handleClose();
      }}
      style={{
        animation: isClosing
          ? 'preview-backdrop-exit 150ms ease-out forwards'
          : 'preview-backdrop 200ms ease-out forwards',
      }}
    >
      <style>{`
        @keyframes preview-backdrop {
          from {
            background-color: rgba(0, 0, 0, 0);
          }
          to {
            background-color: rgba(0, 0, 0, 0.9);
          }
        }
        @keyframes preview-backdrop-exit {
          from {
            background-color: rgba(0, 0, 0, 0.9);
          }
          to {
            background-color: rgba(0, 0, 0, 0);
          }
        }
        @keyframes preview-content {
          from {
            opacity: 0;
            transform: scale(0.95);
          }
          to {
            opacity: 1;
            transform: scale(1);
          }
        }
        @keyframes preview-content-exit {
          from {
            opacity: 1;
            transform: scale(1);
          }
          to {
            opacity: 0;
            transform: scale(0.95);
          }
        }
        /* 隐藏缩略图滚动条 */
        .thumbnail-container::-webkit-scrollbar {
          display: none;
        }
        .thumbnail-container {
          -ms-overflow-style: none;
          scrollbar-width: none;
        }
      `}</style>
      {/* 顶部工具栏 */}
      <div
        className="absolute top-0 left-0 right-0 z-10 flex items-center justify-between px-4 py-3 bg-gradient-to-b from-black/50 to-transparent"
        style={{
          animation: isClosing
            ? 'preview-content-exit 150ms cubic-bezier(0.32, 0.72, 0, 1) forwards'
            : 'preview-content 200ms cubic-bezier(0.32, 0.72, 0, 1) forwards',
        }}
      >
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

          {/* 删除按钮（仅当 onDelete 存在时显示） */}
          {onDelete && (
            <button
              onClick={onDelete}
              className="p-2 text-white/80 hover:text-red-400 hover:bg-white/10 rounded-lg transition-colors"
              title="删除 (Delete)"
            >
              <Trash2 className="w-5 h-5" />
            </button>
          )}

          {/* 关闭 */}
          <button
            onClick={handleClose}
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
        className="w-full h-full flex items-center justify-center overflow-hidden pt-20 pb-28 cursor-default"
        style={{
          animation: isClosing
            ? 'preview-content-exit 150ms cubic-bezier(0.32, 0.72, 0, 1) forwards'
            : 'preview-content 200ms cubic-bezier(0.32, 0.72, 0, 1) forwards',
        }}
        onClick={(e) => {
          // 点击容器背景（非图片）时关闭
          if (e.target === e.currentTarget) handleClose();
        }}
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
          className={`max-w-[90vw] max-h-[calc(100vh-240px)] object-contain select-none ${
            isDragging ? 'cursor-grabbing' : scale > 1 ? 'cursor-grab' : 'cursor-zoom-in'
          }`}
          style={{
            transform: `translate(${position.x}px, ${position.y}px) scale(${scale})`,
            transition: isDragging ? 'none' : 'transform 0.2s ease-out',
          }}
          draggable={false}
        />
      </div>

      {/* 左侧切换按钮 */}
      {hasPrev && onPrev && (
        <button
          onClick={onPrev}
          className="absolute left-4 top-1/2 -translate-y-1/2 z-10 p-3 bg-black/30 hover:bg-black/50 text-white/80 hover:text-white rounded-full transition-all"
          style={{
            animation: isClosing
              ? 'preview-content-exit 150ms cubic-bezier(0.32, 0.72, 0, 1) forwards'
              : 'preview-content 200ms cubic-bezier(0.32, 0.72, 0, 1) forwards',
          }}
          title="上一张 (←)"
        >
          <ChevronLeft className="w-6 h-6" />
        </button>
      )}

      {/* 右侧切换按钮 */}
      {hasNext && onNext && (
        <button
          onClick={onNext}
          className="absolute right-4 top-1/2 -translate-y-1/2 z-10 p-3 bg-black/30 hover:bg-black/50 text-white/80 hover:text-white rounded-full transition-all"
          style={{
            animation: isClosing
              ? 'preview-content-exit 150ms cubic-bezier(0.32, 0.72, 0, 1) forwards'
              : 'preview-content 200ms cubic-bezier(0.32, 0.72, 0, 1) forwards',
          }}
          title="下一张 (→)"
        >
          <ChevronRight className="w-6 h-6" />
        </button>
      )}

      {/* 底部缩略图预览条 */}
      {allImages.length > 0 && onSelectImage && (
        <div
          ref={thumbnailContainerRef}
          className="thumbnail-container absolute bottom-4 left-1/2 -translate-x-1/2 z-10 flex gap-2 px-4 py-2 bg-black/50 rounded-lg backdrop-blur-sm max-w-[90vw] overflow-x-auto"
          style={{
            animation: isClosing
              ? 'preview-content-exit 150ms cubic-bezier(0.32, 0.72, 0, 1) forwards'
              : 'preview-content 200ms cubic-bezier(0.32, 0.72, 0, 1) forwards',
          }}
        >
          {allImages.map((img, index) => (
            <button
              key={index}
              ref={index === currentIndex ? activeThumbnailRef : null}
              type="button"
              onClick={() => onSelectImage(index)}
              className={`flex-shrink-0 h-16 w-16 rounded-lg overflow-hidden transition-all ${
                index === currentIndex
                  ? 'ring-2 ring-white scale-110 shadow-lg'
                  : 'ring-1 ring-white/30 opacity-70 hover:opacity-100 hover:scale-105'
              }`}
              title={`切换到图片 ${index + 1}`}
            >
              <img
                src={img}
                alt={`缩略图 ${index + 1}`}
                className="w-full h-full object-cover"
              />
            </button>
          ))}
        </div>
      )}
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
