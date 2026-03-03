/**
 * AI 多图网格组件
 *
 * 以网格布局展示多张 AI 生成图片：
 * - 2 张: 横排 2 列
 * - 3 张: 横排 3 列
 * - 4 张: 2x2 网格
 *
 * 每个 cell 独立渲染：成功图片 / 加载中占位符 / 失败占位符
 */

import { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import { useInView } from 'react-intersection-observer';
import { Image as ImageIcon, Loader2, AlertTriangle } from 'lucide-react';
import toast from 'react-hot-toast';
import styles from './shared.module.css';
import type { ContentPart } from '../../stores/useMessageStore';

/** 图片加载重试配置 */
const IMAGE_RETRY_CONFIG = {
  maxRetries: 3,
  baseDelay: 1000,
};

interface AiImageGridProps {
  /** 内容数组（包含已完成和未完成的图片） */
  content: ContentPart[];
  /** 预期图片总数 */
  numImages: number;
  /** 消息 ID */
  messageId: string;
  /** 单张图片的占位符尺寸 */
  placeholderSize: { width: number; height: number };
  /** 图片点击回调 */
  onImageClick: (index: number) => void;
  /** 媒体加载完成回调 */
  onMediaLoaded?: () => void;
  /** 是否正在生成中 */
  isGenerating: boolean;
}

/** 网格布局：auto-fill 根据单图宽度自动计算每行列数，放不下自动换行 */

/** 单个网格单元 */
function GridCell({
  imageUrl,
  failed,
  error,
  index,
  messageId,
  placeholderSize,
  onImageClick,
  onMediaLoaded,
  isGenerating,
}: {
  imageUrl: string | null;
  failed?: boolean;
  error?: string;
  index: number;
  messageId: string;
  placeholderSize: { width: number; height: number };
  onImageClick: (index: number) => void;
  onMediaLoaded?: () => void;
  isGenerating: boolean;
}) {
  const [imageLoaded, setImageLoaded] = useState(false);
  const [isDownloading, setIsDownloading] = useState(false);
  const [retryCount, setRetryCount] = useState(0);
  const [loadError, setLoadError] = useState(false);
  const retryTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const { ref: lazyRef, inView } = useInView({
    triggerOnce: true,
    threshold: 0.1,
    rootMargin: '100px',
  });

  const imageUrlWithRetry = useMemo(() => {
    if (!imageUrl) return null;
    if (retryCount === 0) return imageUrl;
    const separator = imageUrl.includes('?') ? '&' : '?';
    return `${imageUrl}${separator}_retry=${retryCount}`;
  }, [imageUrl, retryCount]);

  useEffect(() => {
    if (imageUrl) {
      setImageLoaded(false);
      setRetryCount(0);
      setLoadError(false);
      if (retryTimerRef.current) {
        clearTimeout(retryTimerRef.current);
        retryTimerRef.current = null;
      }
    }
  }, [imageUrl]);

  useEffect(() => {
    return () => {
      if (retryTimerRef.current) clearTimeout(retryTimerRef.current);
    };
  }, []);

  const handleImageError = useCallback(() => {
    if (retryCount < IMAGE_RETRY_CONFIG.maxRetries) {
      const delay = IMAGE_RETRY_CONFIG.baseDelay * Math.pow(2, retryCount);
      retryTimerRef.current = setTimeout(() => {
        setRetryCount((prev) => prev + 1);
      }, delay);
    } else {
      setLoadError(true);
    }
  }, [retryCount]);

  const handleDownload = async (e: React.MouseEvent) => {
    e.stopPropagation();
    if (isDownloading || !imageUrl) return;

    setIsDownloading(true);
    try {
      const response = await fetch(imageUrl, { mode: 'cors', credentials: 'omit' });
      if (!response.ok) throw new Error('download failed');
      const blob = await response.blob();
      const blobUrl = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = blobUrl;
      link.download = `image-${messageId}-${index}.png`;
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      URL.revokeObjectURL(blobUrl);
    } catch {
      toast.error('download failed');
    } finally {
      setIsDownloading(false);
    }
  };

  const aspectRatio = placeholderSize.width / placeholderSize.height;

  // 失败的图片
  if (failed) {
    return (
      <div
        className="rounded-xl bg-gray-100 dark:bg-gray-700 flex flex-col items-center justify-center text-gray-400"
        style={{ aspectRatio }}
      >
        <AlertTriangle className="w-6 h-6 mb-1" />
        <span className="text-xs">{error || '生成失败'}</span>
      </div>
    );
  }

  // 占位符（未完成）— 自适应填充 grid cell，不用固定像素
  if (!imageUrl) {
    return (
      <div
        className="rounded-xl bg-gray-100 dark:bg-gray-700 flex items-center justify-center shadow-sm animate-fade-in animate-media-pulse"
        style={{ aspectRatio }}
      >
        <ImageIcon className="w-10 h-10 text-gray-300 dark:text-gray-500" aria-hidden />
      </div>
    );
  }

  // 加载失败
  if (loadError) {
    return (
      <div
        className="rounded-xl bg-gray-100 flex flex-col items-center justify-center text-gray-500"
        style={{ aspectRatio }}
      >
        <AlertTriangle className="w-6 h-6 mb-1" />
        <span className="text-xs">加载失败</span>
        <button
          type="button"
          className="mt-1 px-2 py-0.5 text-[10px] text-blue-600 hover:bg-blue-50 rounded-full"
          onClick={() => { setLoadError(false); setRetryCount(0); }}
        >
          重试
        </button>
      </div>
    );
  }

  // 正常图片
  const shouldRender = !isGenerating || inView;

  return (
    <div
      ref={lazyRef}
      className={`group cursor-pointer relative rounded-xl overflow-hidden ${styles['dynamic-aspect-ratio']}`}
      style={{ '--aspect-ratio': imageLoaded ? 'auto' : aspectRatio, aspectRatio } as React.CSSProperties}
      role="button"
      tabIndex={0}
      onClick={() => onImageClick(index)}
      onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onImageClick(index); } }}
      aria-label={`查看图片 ${index + 1}`}
    >
      {shouldRender && (
        <img
          src={imageUrlWithRetry || imageUrl}
          alt={`生成的图片 ${index + 1}`}
          className={`w-full h-full object-cover block transition-opacity duration-500 ${imageLoaded ? 'opacity-100' : 'opacity-0'}`}
          onLoad={() => { setImageLoaded(true); onMediaLoaded?.(); }}
          onError={handleImageError}
        />
      )}

      {/* 加载中叠层 */}
      {!imageLoaded && (
        <div className="absolute inset-0 bg-gray-100 dark:bg-gray-700 flex items-center justify-center animate-media-pulse">
          <svg className="w-8 h-8 text-gray-300 dark:text-gray-500" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
            <rect width="18" height="18" x="3" y="3" rx="2" ry="2" />
            <circle cx="9" cy="9" r="2" />
            <path d="m21 15-3.086-3.086a2 2 0 0 0-2.828 0L6 21" />
          </svg>
        </div>
      )}

      {/* 下载按钮 */}
      <div className={`absolute bottom-0 left-0 right-0 flex justify-center py-1.5 bg-gradient-to-t from-black/50 to-transparent transition-opacity ${imageLoaded ? 'opacity-0 group-hover:opacity-100' : 'opacity-0 pointer-events-none'}`}>
        <button
          type="button"
          className="flex items-center gap-1 px-2 py-0.5 text-[10px] text-white bg-black/40 hover:bg-black/60 rounded-full transition-colors disabled:opacity-60"
          disabled={isDownloading}
          onClick={handleDownload}
          aria-label={isDownloading ? '正在下载' : '下载'}
        >
          {isDownloading ? (
            <Loader2 className="w-3 h-3 animate-spin" aria-hidden="true" />
          ) : (
            <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" aria-hidden="true">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4" />
            </svg>
          )}
        </button>
      </div>
    </div>
  );
}

export default function AiImageGrid({
  content,
  numImages,
  messageId,
  placeholderSize,
  onImageClick,
  onMediaLoaded,
  isGenerating,
}: AiImageGridProps) {
  // 构建 cells 数组：确保有 numImages 个 cell
  const cells = useMemo(() => {
    const result: Array<{ url: string | null; failed?: boolean; error?: string }> = [];

    for (let i = 0; i < numImages; i++) {
      const part = content[i];
      if (part && part.type === 'image') {
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        const imgPart = part as any;
        result.push({
          url: imgPart.url || null,
          failed: imgPart.failed || false,
          error: imgPart.error,
        });
      } else {
        // 未到达的 slot
        result.push({ url: null });
      }
    }

    return result;
  }, [content, numImages]);

  return (
    <div className="mt-3 w-full">
      <div className="grid gap-2" style={{ gridTemplateColumns: `repeat(auto-fill, ${placeholderSize.width}px)` }}>
        {cells.map((cell, index) => (
          <GridCell
            key={`${messageId}-cell-${index}`}
            imageUrl={cell.url}
            failed={cell.failed}
            error={cell.error}
            index={index}
            messageId={messageId}
            placeholderSize={placeholderSize}
            onImageClick={onImageClick}
            onMediaLoaded={index === 0 ? onMediaLoaded : undefined}
            isGenerating={isGenerating}
          />
        ))}
      </div>
    </div>
  );
}
