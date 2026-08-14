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

import { memo, useState, useEffect, useMemo } from 'react';
import { createPortal } from 'react-dom';
import { useInView } from 'react-intersection-observer';
import { Image as ImageIcon, Loader2, RefreshCw, XCircle } from 'lucide-react';
import { FailedMediaPlaceholder } from './MediaPlaceholder';
import ImageContextMenu from './ImageContextMenu';
import toast from 'react-hot-toast';
import { downloadImage } from '../../../utils/downloadImage';
import { useThumbnailFallback } from '../../../hooks/useThumbnailFallback';
import { resolveImageOriginalUrl } from '../../../utils/messageUtils';
import {
  getRuntimeMediaImageSlots,
  summarizeRuntimeMediaSlots,
} from '../../../utils/runtimeMediaSlots';
import styles from '../menus/shared.module.css';
import type { ContentPart } from '../../../stores/useMessageStore';
import type { ImageAsset, ImagePart, RuntimeMediaSlotStatus } from '../../../types/message';

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
  /** 单图重新生成回调 */
  onRegenerateSingle?: (imageIndex: number) => void;
  onCancelBatch?: () => void;
}

/** 网格布局：auto-fill 根据单图宽度自动计算每行列数，放不下自动换行 */

/** GridCell props */
interface GridCellProps {
  imageAsset: ImageAsset | null;
  failed?: boolean;
  errorMessage?: string;
  errorCode?: string;
  slotId?: string;
  slotStatus?: RuntimeMediaSlotStatus;
  index: number;
  messageId: string;
  placeholderSize: { width: number; height: number };
  onImageClick: (index: number) => void;
  onMediaLoaded?: () => void;
  isGenerating: boolean;
  onRegenerateSingle?: (imageIndex: number) => void;
}

/** 自定义比较：仅比较数据 props，忽略函数 props（函数行为不变，仅引用变化） */
function gridCellAreEqual(prev: GridCellProps, next: GridCellProps): boolean {
  return (
    prev.imageAsset?.originalUrl === next.imageAsset?.originalUrl &&
    prev.imageAsset?.thumbnailUrl === next.imageAsset?.thumbnailUrl &&
    prev.failed === next.failed &&
    prev.errorMessage === next.errorMessage &&
    prev.errorCode === next.errorCode &&
    prev.slotId === next.slotId &&
    prev.slotStatus === next.slotStatus &&
    prev.index === next.index &&
    prev.messageId === next.messageId &&
    prev.isGenerating === next.isGenerating
  );
}

/** 单个网格单元（memo 包裹，仅数据变化时重渲染） */
const GridCell = memo(function GridCell({
  imageAsset,
  failed,
  errorMessage,
  errorCode,
  slotId,
  slotStatus,
  index,
  messageId,
  placeholderSize,
  onImageClick,
  onMediaLoaded,
  isGenerating,
  onRegenerateSingle,
}: GridCellProps) {
  const [imageLoaded, setImageLoaded] = useState(false);
  const [isDownloading, setIsDownloading] = useState(false);
  const [contextMenu, setContextMenu] = useState<{ x: number; y: number } | null>(null);

  const { ref: lazyRef, inView } = useInView({
    triggerOnce: true,
    threshold: 0.1,
    rootMargin: '100px',
  });
  const thumbnail = useThumbnailFallback(imageAsset?.thumbnailUrl, imageAsset?.originalUrl);

  useEffect(() => {
    if (imageAsset?.originalUrl) {
      setImageLoaded(false);
    }
  }, [imageAsset?.originalUrl]);

  const handleDownload = async (e: React.MouseEvent) => {
    e.stopPropagation();
    if (isDownloading || !imageAsset?.originalUrl) return;

    setIsDownloading(true);
    try {
      await downloadImage(imageAsset.originalUrl, `image-${messageId}-${index}`);
    } catch {
      toast.error('download failed');
    } finally {
      setIsDownloading(false);
    }
  };

  const aspectRatio = placeholderSize.width / placeholderSize.height;
  const isRuntimeSlot = slotId !== undefined && slotStatus !== undefined;
  const isRuntimeRetryable = slotStatus === 'failed' || slotStatus === 'cancelled';

  // Runtime 只允许失败/取消槽重试；legacy 继续沿用原失败重试行为。
  if (failed || isRuntimeRetryable) {
    return (
      <div data-slot-id={slotId} data-slot-status={slotStatus || 'failed'}>
        <FailedMediaPlaceholder
          type="image"
          aspectRatio={aspectRatio}
          onRetry={onRegenerateSingle && (!isRuntimeSlot || isRuntimeRetryable)
            ? () => onRegenerateSingle(index) : undefined}
          retryLabel="重新生成"
          errorMessage={errorMessage || (slotStatus === 'cancelled'
            ? '图片生成已取消' : '图片生成失败')}
          errorCode={errorCode}
        />
      </div>
    );
  }

  // Runtime 槽位按状态保留，便于后续批次取消和 reconcile 直接复用同一状态面。
  if (!imageAsset?.originalUrl) {
    const label = slotStatus === 'accepted' ? '已提交，正在生成'
      : slotStatus === 'unknown' ? '正在确认生成结果'
        : slotStatus === 'completed' ? '正在同步生成结果'
          : '等待生成';
    return (
      <div
        className="rounded-xl bg-hover dark:bg-surface-dark-card flex items-center justify-center shadow-sm animate-fade-in animate-media-pulse"
        style={{ aspectRatio }}
        data-slot-id={slotId}
        data-slot-status={slotStatus || 'pending'}
        aria-label={label}
      >
        <div className="flex flex-col items-center gap-2 text-text-disabled dark:text-text-tertiary">
          <ImageIcon className="w-10 h-10" aria-hidden />
          {slotStatus && <span className="text-xs">{label}</span>}
        </div>
      </div>
    );
  }

  // 加载失败
  if (thumbnail.failed) {
    return (
      <FailedMediaPlaceholder
        type="image"
        aspectRatio={aspectRatio}
        onRetry={thumbnail.reset}
        retryLabel="重试加载"
      />
    );
  }

  // 正常图片
  const shouldRender = !isGenerating || inView;

  return (
    <div
      ref={lazyRef}
      className={`group cursor-pointer relative rounded-xl overflow-hidden ${styles['dynamic-aspect-ratio']}`}
      style={{ '--aspect-ratio': imageLoaded ? 'auto' : aspectRatio, aspectRatio } as React.CSSProperties}
      data-slot-id={slotId}
      data-slot-status={slotStatus || undefined}
      role="button"
      tabIndex={0}
      onClick={() => onImageClick(index)}
      onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onImageClick(index); } }}
      onContextMenu={(e) => { if (imageLoaded && imageAsset.originalUrl) { e.preventDefault(); setContextMenu({ x: e.clientX, y: e.clientY }); } }}
      aria-label={`查看图片 ${index + 1}`}
    >
      {shouldRender && (
        <img
          src={thumbnail.src}
          alt={`生成的图片 ${index + 1}`}
          className={`w-full h-full object-cover block transition-opacity duration-200 ${imageLoaded ? 'opacity-100' : 'opacity-0'}`}
          onLoad={() => { setImageLoaded(true); onMediaLoaded?.(); }}
          onError={thumbnail.onError}
        />
      )}

      {/* 加载中叠层 */}
      {!imageLoaded && (
        <div className="absolute inset-0 bg-hover dark:bg-surface-dark-card flex items-center justify-center animate-media-pulse">
          <svg className="w-8 h-8 text-text-disabled dark:text-text-tertiary" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
            <rect width="18" height="18" x="3" y="3" rx="2" ry="2" />
            <circle cx="9" cy="9" r="2" />
            <path d="m21 15-3.086-3.086a2 2 0 0 0-2.828 0L6 21" />
          </svg>
        </div>
      )}

      {/* 悬浮操作按钮 */}
      <div className={`absolute bottom-0 left-0 right-0 flex justify-center gap-1.5 py-1.5 bg-gradient-to-t from-black/50 to-transparent transition-opacity ${imageLoaded ? 'opacity-0 group-hover:opacity-100' : 'opacity-0 pointer-events-none'}`}>
        {onRegenerateSingle && !isRuntimeSlot && (
          <button
            type="button"
            className="flex items-center px-2 py-0.5 text-white bg-black/40 hover:bg-black/60 rounded-full transition-base"
            onClick={(e) => { e.stopPropagation(); onRegenerateSingle?.(index); }}
            aria-label="重新生成"
          >
            <RefreshCw className="w-3 h-3" />
          </button>
        )}
        <button
          type="button"
          className="flex items-center px-2 py-0.5 text-white bg-black/40 hover:bg-black/60 rounded-full transition-base disabled:opacity-60"
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

      {/* 右键上下文菜单（Portal 到 body，避免被 overflow-hidden 裁剪） */}
      {contextMenu && imageAsset.originalUrl && createPortal(
        <ImageContextMenu
          x={contextMenu.x}
          y={contextMenu.y}
          imageUrl={imageAsset.originalUrl}
          thumbnailUrl={imageAsset.thumbnailUrl}
          messageId={messageId}
          onClose={() => setContextMenu(null)}
        />,
        document.body,
      )}
    </div>
  );
}, gridCellAreEqual);

export default function AiImageGrid({
  content,
  numImages,
  messageId,
  placeholderSize,
  onImageClick,
  onMediaLoaded,
  isGenerating,
  onRegenerateSingle,
  onCancelBatch,
}: AiImageGridProps) {
  const runtimeSlots = useMemo(() => getRuntimeMediaImageSlots(content), [content]);
  const runtimeSummary = useMemo(
    () => runtimeSlots.length > 0 ? summarizeRuntimeMediaSlots(runtimeSlots) : null,
    [runtimeSlots],
  );

  // Runtime 使用稳定 slot_index；历史纯图片链路继续按图片集合顺序兼容。
  const cells = useMemo(() => {
    const result: Array<{
      asset: ImageAsset | null;
      failed?: boolean;
      errorMessage?: string;
      errorCode?: string;
      slotId?: string;
      slotIndex?: number;
      slotStatus?: RuntimeMediaSlotStatus;
    }> = [];
    if (runtimeSlots.length > 0) {
      runtimeSlots.forEach((imgPart) => {
        const originalUrl = resolveImageOriginalUrl(imgPart);
        result.push({
          asset: originalUrl ? {
            originalUrl,
            ...(imgPart.thumbnail_url ? { thumbnailUrl: imgPart.thumbnail_url } : {}),
            ...(imgPart.alt ? { alt: imgPart.alt } : {}),
            ...(imgPart.width ? { width: imgPart.width } : {}),
            ...(imgPart.height ? { height: imgPart.height } : {}),
            sourcePart: imgPart,
          } : null,
          failed: imgPart.slot_status === 'failed' || !!imgPart.failed,
          ...(imgPart.error ? { errorMessage: imgPart.error } : {}),
          ...(imgPart.error_code ? { errorCode: imgPart.error_code } : {}),
          slotId: imgPart.slot_id,
          slotIndex: imgPart.slot_index,
          slotStatus: imgPart.slot_status,
        });
      });
      return result;
    }

    const imageParts = content.filter((part): part is ImagePart => part.type === 'image');
    const cellCount = isGenerating ? Math.max(numImages, imageParts.length) : imageParts.length;

    for (let i = 0; i < cellCount; i++) {
      const imgPart = imageParts[i];
      if (imgPart) {
        const originalUrl = resolveImageOriginalUrl(imgPart);
        result.push({
          asset: originalUrl ? {
            originalUrl,
            ...(imgPart.thumbnail_url ? { thumbnailUrl: imgPart.thumbnail_url } : {}),
            ...(imgPart.alt ? { alt: imgPart.alt } : {}),
            ...(imgPart.width ? { width: imgPart.width } : {}),
            ...(imgPart.height ? { height: imgPart.height } : {}),
            sourcePart: imgPart,
          } : null,
          failed: imgPart.failed || false,
          ...(imgPart.error ? { errorMessage: imgPart.error } : {}),
          ...(imgPart.error_code ? { errorCode: imgPart.error_code } : {}),
        });
      } else {
        // 未到达的 slot
        result.push({ asset: null });
      }
    }

    return result;
  }, [content, isGenerating, numImages, runtimeSlots]);

  return (
    <div className="mt-3 w-full">
      <div className="grid gap-2" style={{ gridTemplateColumns: `repeat(auto-fill, ${placeholderSize.width}px)` }}>
        {cells.map((cell, index) => (
          <GridCell
            key={cell.slotId || `${messageId}-cell-${index}`}
            imageAsset={cell.asset}
            failed={cell.failed}
            errorMessage={cell.errorMessage}
            errorCode={cell.errorCode}
            slotId={cell.slotId}
            slotStatus={cell.slotStatus}
            index={cell.slotIndex ?? index}
            messageId={messageId}
            placeholderSize={placeholderSize}
            onImageClick={onImageClick}
            onMediaLoaded={runtimeSlots.length > 0 || index === 0 ? onMediaLoaded : undefined}
            isGenerating={isGenerating}
            onRegenerateSingle={onRegenerateSingle}
          />
        ))}
      </div>
      {runtimeSummary && (
        <div
          className="mt-2 text-xs text-text-tertiary"
          data-testid="runtime-media-summary"
          data-active-count={runtimeSummary.active}
          aria-live="polite"
        >
          {runtimeSummary.completed}/{runtimeSummary.total} 已完成
          {runtimeSummary.failed > 0 && ` · ${runtimeSummary.failed} 失败`}
          {runtimeSummary.cancelled > 0 && ` · ${runtimeSummary.cancelled} 已取消`}
          {runtimeSummary.unknown > 0 && ` · ${runtimeSummary.unknown} 结果确认中`}
          {onCancelBatch && runtimeSummary.active > 0 && (
            <button
              type="button"
              className="ml-2 inline-flex items-center gap-1 text-text-secondary hover:text-text-primary"
              onClick={onCancelBatch}
              aria-label="停止生成"
            >
              <XCircle className="w-3 h-3" /> 停止
            </button>
          )}
        </div>
      )}
    </div>
  );
}
