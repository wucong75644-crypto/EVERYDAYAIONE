import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { useInView } from 'react-intersection-observer';
import { Loader2 } from 'lucide-react';
import toast from 'react-hot-toast';
import { downloadImage } from '../../../utils/downloadImage';
import { toThumbnailImageUrl } from '../../../utils/imageUrlRules';
import MediaPlaceholder from '../media/MediaPlaceholder';
import ImageContextMenu from '../media/ImageContextMenu';
import styles from '../menus/shared.module.css';
import type { ImageAsset } from '../../../types/message';

const IMAGE_RETRY_CONFIG = {
  maxRetries: 3,
  baseDelay: 1000,
};

interface ImageBlockProps {
  messageId: string;
  onMediaLoaded?: () => void;
}

export function AiGeneratedImage({
  imageAsset,
  messageId,
  placeholderSize,
  onImageClick,
  onMediaLoaded,
  isGenerating,
}: ImageBlockProps & {
  imageAsset: ImageAsset | null;
  placeholderSize: { width: number; height: number };
  onImageClick: () => void;
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
  const imageUrl = imageAsset?.originalUrl || null;
  const displayImageUrl = useMemo(
    () => imageAsset?.thumbnailUrl || toThumbnailImageUrl(imageAsset?.originalUrl, Math.ceil(placeholderSize.width)),
    [imageAsset, placeholderSize.width],
  );

  const imageUrlWithRetry = useMemo(() => {
    if (!displayImageUrl) return null;
    if (retryCount === 0) return displayImageUrl;
    const separator = displayImageUrl.includes('?') ? '&' : '?';
    return `${displayImageUrl}${separator}_retry=${retryCount}`;
  }, [displayImageUrl, retryCount]);

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

  const showPlaceholder = !imageLoaded && !loadError && (isGenerating || !!imageUrl);
  useEffect(() => {
    if (showPlaceholder && !placeholderNotified.current) {
      placeholderNotified.current = true;
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
      {isGenerating && !imageUrl && (
        <MediaPlaceholder type="image" width={placeholderSize.width} height={placeholderSize.height} />
      )}

      {imageUrl && shouldRender && !loadError && (
        <div
          className={`group cursor-pointer relative inline-block ${styles['dynamic-aspect-ratio']}`}
          style={
            {
              '--aspect-ratio': imageLoaded ? 'auto' : aspectRatio,
              '--max-width': `${placeholderSize.width}px`,
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
            src={imageUrlWithRetry || displayImageUrl}
            alt="生成的图片"
            className={`rounded-xl shadow-sm w-full h-auto block transition-opacity duration-200 ${imageLoaded ? 'opacity-100' : 'opacity-0'}`}
            onLoad={() => {
              setImageLoaded(true);
              onMediaLoaded?.();
            }}
            onError={handleImageError}
          />
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
              thumbnailUrl={imageAsset?.thumbnailUrl}
              messageId={messageId}
              onClose={() => setContextMenu(null)}
            />,
            document.body,
          )}
        </div>
      )}

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

function UserImage({
  imageAsset,
  index,
  messageId,
  onImageClick,
  onMediaLoaded,
}: ImageBlockProps & {
  imageAsset: ImageAsset;
  index: number;
  onImageClick: (index: number) => void;
}) {
  const [contextMenu, setContextMenu] = useState<{ x: number; y: number } | null>(null);
  const displayImageUrl = useMemo(
    () => imageAsset.thumbnailUrl || toThumbnailImageUrl(imageAsset.originalUrl, 360),
    [imageAsset],
  );

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
        src={displayImageUrl}
        alt={`上传的图片 ${index + 1}`}
        className="rounded-xl shadow-sm w-full h-auto block"
        onLoad={onMediaLoaded}
      />
      {contextMenu && createPortal(
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
}

export function UserImageGallery({
  imageAssets,
  messageId,
  maxWidth,
  onImageClick,
  onMediaLoaded,
}: ImageBlockProps & {
  imageAssets: ImageAsset[];
  maxWidth: number;
  onImageClick: (index: number) => void;
}) {
  if (imageAssets.length === 0) return null;

  return (
    <div
      className="mt-4 grid gap-2 w-full justify-end"
      style={{ gridTemplateColumns: `repeat(auto-fit, ${maxWidth}px)` }}
    >
      {imageAssets.map((asset, index) => (
        <UserImage
          key={`${asset.originalUrl}-${index}`}
          imageAsset={asset}
          index={index}
          messageId={messageId}
          onImageClick={onImageClick}
          onMediaLoaded={index === 0 ? onMediaLoaded : undefined}
        />
      ))}
    </div>
  );
}
