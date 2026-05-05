/** 消息媒体组件 - 渲染消息中的图片和视频内容 */

import { memo, useState, useEffect, useMemo, useCallback, useRef } from 'react';
import { createPortal } from 'react-dom';
import { useInView } from 'react-intersection-observer';
import { Loader2 } from 'lucide-react';
import toast from 'react-hot-toast';
import { downloadImage } from '../../../utils/downloadImage';
import { type AspectRatio, type VideoAspectRatio } from '../../../constants/models';
import { getImagePlaceholderSize, getVideoPlaceholderSize } from '../../../utils/settingsStorage';
import MediaPlaceholder, { FailedMediaPlaceholder } from '../media/MediaPlaceholder';
import AiImageGrid from '../media/AiImageGrid';
import ImageContextMenu from '../media/ImageContextMenu';
import styles from '../menus/shared.module.css';
import type { ContentPart, FilePart } from '../../../types/message';
import FileCardList from '../media/FileCard';

const IMAGE_RETRY_CONFIG = {
  maxRetries: 3,
  baseDelay: 1000, // 基础延迟 1s，指数退避
};

interface MessageMediaProps {
  /** 图片 URL 列表 */
  imageUrls?: string[];
  /** 视频 URL 列表 */
  videoUrls?: string[];
  /** 消息 ID（用于下载文件命名） */
  messageId: string;
  /** 是否为用户消息 */
  isUser: boolean;
  /** 图片点击回调（打开预览） */
  onImageClick: (index?: number) => void;
  /** 媒体加载完成回调（用于滚动调整） */
  onMediaLoaded?: () => void;
  /** 是否正在生成中（显示占位符） */
  isGenerating?: boolean;
  /** 生成类型（image/video） */
  generatingType?: 'image' | 'video';
  /** 图片宽高比（用于 AI 生成图片的占位符尺寸） */
  imageAspectRatio?: AspectRatio;
  /** 视频宽高比（用于占位符动态尺寸） */
  videoAspectRatio?: VideoAspectRatio;
  /** 预期图片数量（多图模式） */
  numImages?: number;
  /** 文件列表 */
  files?: FilePart[];
  /** 消息的完整 content 数组（多图模式需要） */
  content?: ContentPart[];
  /** 单图重新生成回调（多图模式） */
  onRegenerateSingle?: (imageIndex: number) => void;
  /** 失败的媒体类型（显示裂开占位符） */
  failedMediaType?: 'image' | 'video' | null;
  /** 重新生成回调（失败时 retry） */
  onRegenerate?: () => void;
}

/** 单张图片组件（AI 生成，带占位符和失败重试） */
function AiGeneratedImage({
  imageUrl,
  messageId,
  placeholderSize,
  onImageClick,
  onMediaLoaded,
  isGenerating,
}: {
  imageUrl: string | null;
  messageId: string;
  placeholderSize: { width: number; height: number };
  onImageClick: () => void;
  onMediaLoaded?: () => void;
  isGenerating: boolean;
}) {
  const [imageLoaded, setImageLoaded] = useState(false);
  const [isDownloading, setIsDownloading] = useState(false);
  const [retryCount, setRetryCount] = useState(0);
  const [loadError, setLoadError] = useState(false);
  const [contextMenu, setContextMenu] = useState<{ x: number; y: number } | null>(null);
  const placeholderNotified = useRef(false);
  const retryTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const { ref: lazyRef, inView } = useInView({
    triggerOnce: true,
    threshold: 0.1,
    rootMargin: '100px',
  });
  const shouldRender = !isGenerating || inView;

  const aspectRatio = placeholderSize.width / placeholderSize.height;

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
      placeholderNotified.current = false;
      if (retryTimerRef.current) {
        clearTimeout(retryTimerRef.current);
        retryTimerRef.current = null;
      }
    }
  }, [imageUrl]);

  useEffect(() => {
    return () => {
      if (retryTimerRef.current) {
        clearTimeout(retryTimerRef.current);
      }
    };
  }, []);

  const handleImageError = useCallback(() => {
    if (retryCount < IMAGE_RETRY_CONFIG.maxRetries) {
      // 指数退避延迟重试
      const delay = IMAGE_RETRY_CONFIG.baseDelay * Math.pow(2, retryCount);
      retryTimerRef.current = setTimeout(() => {
        setRetryCount((prev) => prev + 1);
      }, delay);
    } else {
      // 重试次数用尽，标记加载失败
      setLoadError(true);
    }
  }, [retryCount]);

  // 占位符可见：图片未加载完成且无加载错误时，生成中或有URL正在加载都显示
  // 修复：任务完成（isGenerating=false）后图片还没 onLoad 时的空白间隙
  const showPlaceholder = !imageLoaded && !loadError && (isGenerating || !!imageUrl);
  useEffect(() => {
    if (showPlaceholder && !placeholderNotified.current) {
      placeholderNotified.current = true;
      // 使用 requestAnimationFrame 确保 DOM 渲染完成后再触发
      requestAnimationFrame(() => {
        onMediaLoaded?.();
      });
    }
  }, [showPlaceholder, onMediaLoaded]);

  const handleDownload = async (e: React.MouseEvent) => {
    e.stopPropagation();
    if (isDownloading || !imageUrl) return;

    setIsDownloading(true);
    try {
      await downloadImage(imageUrl, `image-${messageId}`);
    } catch {
      toast.error('下载失败，请右键图片选择"另存为"');
    } finally {
      setIsDownloading(false);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      onImageClick();
    }
  };

  return (
    <div className="mt-3 leading-none" ref={lazyRef}>
      {/* 纯占位符：生成中且还没有 imageUrl */}
      {isGenerating && !imageUrl && (
        <MediaPlaceholder
          type="image"
          width={placeholderSize.width}
          height={placeholderSize.height}
        />
      )}

      {/* 图片容器：有 imageUrl 后渲染，占位符叠加在图片上方实现平滑过渡 */}
      {imageUrl && shouldRender && !loadError && (
        <div
          className={`group cursor-pointer relative inline-block ${styles['dynamic-aspect-ratio']}`}
          style={
            {
              '--aspect-ratio': imageLoaded ? 'auto' : aspectRatio,
              '--max-width': `${placeholderSize.width}px`,
              // 图片加载前用显式宽度撑开容器，否则 inline-block 会塌缩为 0
              ...(imageLoaded ? {} : { width: `${placeholderSize.width}px` }),
            } as React.CSSProperties
          }
          role="button"
          tabIndex={0}
          onClick={onImageClick}
          onKeyDown={handleKeyDown}
          onContextMenu={(e) => { if (imageLoaded && imageUrl) { e.preventDefault(); setContextMenu({ x: e.clientX, y: e.clientY }); } }}
          aria-label="查看大图"
        >
          <img
            src={imageUrlWithRetry || imageUrl}
            alt="生成的图片"
            className={`rounded-xl shadow-sm w-full h-auto block transition-opacity duration-200 ${imageLoaded ? 'opacity-100' : 'opacity-0'}`}
            onLoad={() => {
              setImageLoaded(true);
              onMediaLoaded?.();
            }}
            onError={handleImageError}
          />
          {/* 占位符叠层：图片加载期间覆盖在上方，加载完成后移除 */}
          {!imageLoaded && (
            <div className="absolute inset-0 rounded-xl bg-hover dark:bg-surface-dark-card flex items-center justify-center animate-media-pulse">
              <svg className="w-10 h-10 text-text-disabled dark:text-text-tertiary" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                <rect width="18" height="18" x="3" y="3" rx="2" ry="2" />
                <circle cx="9" cy="9" r="2" />
                <path d="m21 15-3.086-3.086a2 2 0 0 0-2.828 0L6 21" />
              </svg>
            </div>
          )}
          <div className={`absolute bottom-0 left-0 right-0 flex justify-center py-2 bg-gradient-to-t from-black/50 to-transparent rounded-b-xl transition-opacity ${imageLoaded ? 'opacity-0 group-hover:opacity-100' : 'opacity-0 pointer-events-none'}`}>
            <button
              type="button"
              className="flex items-center gap-1 px-3 py-1 text-xs text-white bg-black/40 hover:bg-black/60 rounded-full transition-base disabled:opacity-60"
              disabled={isDownloading}
              onClick={handleDownload}
              aria-label={isDownloading ? '正在下载图片' : '下载图片'}
            >
              {isDownloading ? (
                <Loader2 className="w-3.5 h-3.5 animate-spin" aria-hidden="true" />
              ) : (
                <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" aria-hidden="true">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4" />
                </svg>
              )}
              <span>{isDownloading ? '下载中' : '下载'}</span>
            </button>
          </div>

          {contextMenu && imageUrl && createPortal(
            <ImageContextMenu
              x={contextMenu.x}
              y={contextMenu.y}
              imageUrl={imageUrl}
              messageId={messageId}
              onClose={() => setContextMenu(null)}
            />,
            document.body,
          )}
        </div>
      )}

      {/* 加载失败提示 */}
      {loadError && imageUrl && (
        <div
          className="flex flex-col items-center justify-center rounded-xl bg-hover text-text-tertiary"
          style={{ width: placeholderSize.width, height: placeholderSize.height }}
        >
          <svg className="w-8 h-8 mb-2" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
          </svg>
          <span className="text-sm">图片加载失败</span>
          <button
            type="button"
            className="mt-2 px-3 py-1 text-xs text-accent hover:text-accent-hover hover:bg-accent-light rounded-full transition-base"
            onClick={() => {
              setLoadError(false);
              setRetryCount(0);
            }}
          >
            点击重试
          </button>
        </div>
      )}
    </div>
  );
}

/** 单张用户图片组件（无占位符，自适应尺寸，支持右键引用） */
function UserImage({
  imageUrl,
  index,
  messageId,
  onImageClick,
  onMediaLoaded,
}: {
  imageUrl: string;
  index: number;
  messageId: string;
  onImageClick: (index: number) => void;
  onMediaLoaded?: () => void;
}) {
  const [contextMenu, setContextMenu] = useState<{ x: number; y: number } | null>(null);

  const handleClick = useCallback(() => {
    onImageClick(index);
  }, [index, onImageClick]);

  const handleKeyDown = useCallback((e: React.KeyboardEvent) => {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      onImageClick(index);
    }
  }, [index, onImageClick]);

  return (
    <div
      className="group cursor-pointer relative"
      role="button"
      tabIndex={0}
      onClick={handleClick}
      onKeyDown={handleKeyDown}
      onContextMenu={(e) => { e.preventDefault(); setContextMenu({ x: e.clientX, y: e.clientY }); }}
      aria-label={`查看图片 ${index + 1}`}
    >
      <img
        src={imageUrl}
        alt={`上传的图片 ${index + 1}`}
        className="rounded-xl shadow-sm w-full h-auto block"
        onLoad={onMediaLoaded}
      />
      {contextMenu && createPortal(
        <ImageContextMenu
          x={contextMenu.x}
          y={contextMenu.y}
          imageUrl={imageUrl}
          messageId={messageId}
          onClose={() => setContextMenu(null)}
        />,
        document.body,
      )}
    </div>
  );
}

/** 用户图片容器（auto-fill 网格，与 AI 图片统一布局逻辑） */
function UserImageGallery({
  imageUrls,
  messageId,
  maxWidth,
  onImageClick,
  onMediaLoaded,
}: {
  imageUrls: string[];
  messageId: string;
  maxWidth: number;
  onImageClick: (index: number) => void;
  onMediaLoaded?: () => void;
}) {
  if (imageUrls.length === 0) return null;

  return (
    <div
      className="mt-4 grid gap-2 w-full justify-end"
      style={{ gridTemplateColumns: `repeat(auto-fit, ${maxWidth}px)` }}
    >
      {imageUrls.map((url, index) => (
        <UserImage
          key={`${url}-${index}`}
          imageUrl={url}
          index={index}
          messageId={messageId}
          onImageClick={onImageClick}
          onMediaLoaded={index === 0 ? onMediaLoaded : undefined}
        />
      ))}
    </div>
  );
}

export default memo(function MessageMedia({
  imageUrls = [],
  videoUrls = [],
  files = [],
  messageId,
  isUser,
  onImageClick,
  onMediaLoaded,
  isGenerating = false,
  generatingType = 'image',
  imageAspectRatio = '1:1',
  videoAspectRatio = 'landscape',
  numImages = 1,
  content = [],
  onRegenerateSingle,
  failedMediaType,
  onRegenerate,
}: MessageMediaProps) {
  const videoUrl = videoUrls[0] || null;

  const imagePlaceholderSize = useMemo(
    () => getImagePlaceholderSize(imageAspectRatio),
    [imageAspectRatio]
  );
  const videoPlaceholderSize = useMemo(
    () => getVideoPlaceholderSize(videoAspectRatio),
    [videoAspectRatio]
  );

  const { ref: videoLazyRef, inView: videoInView } = useInView({
    triggerOnce: true,
    threshold: 0.1,
    rootMargin: '100px',
  });

  const videoPlaceholderNotified = useRef(false);
  const showVideoPlaceholder = isGenerating && generatingType === 'video' && !videoUrl;
  useEffect(() => {
    if (showVideoPlaceholder && !videoPlaceholderNotified.current) {
      videoPlaceholderNotified.current = true;
      requestAnimationFrame(() => {
        onMediaLoaded?.();
      });
    }
  }, [showVideoPlaceholder, onMediaLoaded]);

  const handleImageClick = useCallback((index?: number) => {
    onImageClick(index ?? 0);
  }, [onImageClick]);

  if (imageUrls.length === 0 && !videoUrl && !isGenerating && !failedMediaType && files.length === 0) return null;

  return (
    <>
      {/* 图片渲染 */}
      {(imageUrls.length > 0 || (isGenerating && generatingType === 'image')) && (
        isUser ? (
          // 用户图片：直接显示，无占位符，支持多图横排
          <UserImageGallery
            imageUrls={imageUrls}
            messageId={messageId}
            maxWidth={imagePlaceholderSize.width}
            onImageClick={handleImageClick}
            onMediaLoaded={onMediaLoaded}
          />
        ) : numImages > 1 ? (
          // AI 多图：网格布局
          <AiImageGrid
            content={content}
            numImages={numImages}
            messageId={messageId}
            placeholderSize={imagePlaceholderSize}
            onImageClick={handleImageClick}
            onMediaLoaded={onMediaLoaded}
            isGenerating={isGenerating && generatingType === 'image'}
            onRegenerateSingle={onRegenerateSingle}
          />
        ) : (
          // AI 单图：占位符 + 淡入效果
          <AiGeneratedImage
            imageUrl={imageUrls[0] || null}
            messageId={messageId}
            placeholderSize={imagePlaceholderSize}
            onImageClick={() => handleImageClick(0)}
            onMediaLoaded={onMediaLoaded}
            isGenerating={isGenerating && generatingType === 'image'}
          />
        )
      )}

      {/* 失败的图片占位符（裂开状态 + hover 重新生成） */}
      {failedMediaType === 'image' && imageUrls.length === 0 && !isGenerating && (
        <div className="mt-3">
          <FailedMediaPlaceholder
            type="image"
            width={imagePlaceholderSize.width}
            height={imagePlaceholderSize.height}
            onRetry={onRegenerate}
          />
        </div>
      )}

      {/* 视频渲染（含占位符） */}
      {(videoUrl || (isGenerating && generatingType === 'video')) && (
        <div className="mt-3" ref={videoLazyRef}>
          {/* 视频占位符（带淡入动画） */}
          {isGenerating && generatingType === 'video' && !videoUrl && (
            <MediaPlaceholder
              type="video"
              width={videoPlaceholderSize.width}
              height={videoPlaceholderSize.height}
            />
          )}

          {/* 视频（按占位符尺寸限制显示） */}
          {videoUrl && (!isGenerating || videoInView) && (
            <video
              src={videoUrl}
              controls
              className={`${styles['dynamic-max-width']} rounded-xl shadow-sm w-full h-auto block`}
              style={
                {
                  '--max-width': `${videoPlaceholderSize.width}px`,
                } as React.CSSProperties
              }
              preload="metadata"
              onLoadedMetadata={() => onMediaLoaded?.()}
            >
              您的浏览器不支持视频播放
            </video>
          )}
        </div>
      )}

      {/* 失败的视频占位符（裂开状态 + hover 重新生成） */}
      {failedMediaType === 'video' && !videoUrl && !isGenerating && (
        <div className="mt-3">
          <FailedMediaPlaceholder
            type="video"
            width={videoPlaceholderSize.width}
            height={videoPlaceholderSize.height}
            onRetry={onRegenerate}
          />
        </div>
      )}

      {/* 文件下载卡片 */}
      {files.length > 0 && <FileCardList files={files} />}
    </>
  );
});


