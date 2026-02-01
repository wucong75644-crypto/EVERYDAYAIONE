/**
 * 消息媒体组件
 *
 * 负责渲染消息中的图片和视频内容
 * - 用户图片：直接显示，自适应尺寸，支持多图横排
 * - AI 图片：占位符 + 淡入效果，固定尺寸
 */

import { useState, useEffect, useMemo, useCallback, useRef } from 'react';
import { useInView } from 'react-intersection-observer';
import { Loader2 } from 'lucide-react';
import toast from 'react-hot-toast';
import { type AspectRatio } from '../../services/image';
import { type VideoAspectRatio } from '../../services/video';
import { getImagePlaceholderSize, getVideoPlaceholderSize } from '../../utils/settingsStorage';
import { parseImageUrls } from '../../utils/imageUtils';
import MediaPlaceholder from './MediaPlaceholder';
import styles from './shared.module.css';

interface MessageMediaProps {
  /** 图片 URL（单个或多个，逗号分隔） */
  imageUrl?: string | null;
  /** 视频 URL */
  videoUrl?: string | null;
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
}

/** 单张图片组件（AI 生成，带占位符） */
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
  const placeholderNotified = useRef(false);

  // 懒加载
  const { ref: lazyRef, inView } = useInView({
    triggerOnce: true,
    threshold: 0.1,
    rootMargin: '100px',
  });
  const shouldRender = !isGenerating || inView;

  // 计算宽高比（用于图片加载前的占位）
  const aspectRatio = placeholderSize.width / placeholderSize.height;

  // imageUrl 变化时重置
  useEffect(() => {
    if (imageUrl) {
      setImageLoaded(false);
      placeholderNotified.current = false;
    }
  }, [imageUrl]);

  // 占位符渲染时触发滚动回调（仅触发一次）
  const showPlaceholder = isGenerating && !imageLoaded;
  useEffect(() => {
    if (showPlaceholder && !placeholderNotified.current) {
      placeholderNotified.current = true;
      // 使用 requestAnimationFrame 确保 DOM 渲染完成后再触发
      requestAnimationFrame(() => {
        onMediaLoaded?.();
      });
    }
  }, [showPlaceholder, onMediaLoaded]);

  // 下载图片
  const handleDownload = async (e: React.MouseEvent) => {
    e.stopPropagation();
    if (isDownloading || !imageUrl) return;

    setIsDownloading(true);
    try {
      const response = await fetch(imageUrl, { mode: 'cors', credentials: 'omit' });
      if (!response.ok) throw new Error('下载失败');

      const blob = await response.blob();
      const blobUrl = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = blobUrl;
      link.download = `image-${messageId}.png`;
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      URL.revokeObjectURL(blobUrl);
    } catch {
      toast.error('下载失败，请右键图片选择"另存为"');
    } finally {
      setIsDownloading(false);
    }
  };

  // 键盘事件处理（支持回车和空格键）
  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      onImageClick();
    }
  };

  return (
    <div className="mt-3" ref={lazyRef}>
      {/* 占位符（仅生成中显示固定尺寸，带淡入动画） */}
      {showPlaceholder && (
        <MediaPlaceholder
          type="image"
          width={placeholderSize.width}
          height={placeholderSize.height}
        />
      )}

      {/* 图片（按占位符尺寸限制显示） */}
      {imageUrl && shouldRender && (
        <div
          className={`group cursor-pointer relative inline-block ${styles['dynamic-aspect-ratio']}`}
          // 动态宽高比需要 CSS 变量
          style={
            {
              '--aspect-ratio': imageLoaded ? 'auto' : aspectRatio,
              '--max-width': `${placeholderSize.width}px`,
            } as React.CSSProperties
          }
          role="button"
          tabIndex={0}
          onClick={onImageClick}
          onKeyDown={handleKeyDown}
          aria-label="查看大图"
        >
          <img
            src={imageUrl}
            alt="生成的图片"
            className="rounded-xl shadow-sm w-full h-auto block"
            onLoad={() => {
              setImageLoaded(true);
              onMediaLoaded?.();
            }}
          />
          {/* 下载按钮 */}
          <div className="absolute bottom-0 left-0 right-0 flex justify-center py-2 bg-gradient-to-t from-black/50 to-transparent rounded-b-xl opacity-0 group-hover:opacity-100 transition-opacity">
            <button
              type="button"
              className="flex items-center gap-1 px-3 py-1 text-xs text-white bg-black/40 hover:bg-black/60 rounded-full transition-colors disabled:opacity-60"
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
        </div>
      )}
    </div>
  );
}

/** 单张用户图片组件（无占位符，自适应尺寸） */
function UserImage({
  imageUrl,
  index,
  maxWidth,
  onImageClick,
  onMediaLoaded,
}: {
  imageUrl: string;
  index: number;
  maxWidth: number;
  onImageClick: (index: number) => void;
  onMediaLoaded?: () => void;
}) {
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
      className={`group cursor-pointer relative inline-block ${styles['dynamic-max-width']}`}
      role="button"
      tabIndex={0}
      onClick={handleClick}
      onKeyDown={handleKeyDown}
      aria-label={`查看图片 ${index + 1}`}
      // 动态最大宽度需要 CSS 变量
      style={
        {
          '--max-width': `${maxWidth}px`,
        } as React.CSSProperties
      }
    >
      <img
        src={imageUrl}
        alt={`上传的图片 ${index + 1}`}
        className="rounded-xl shadow-sm w-full h-auto block"
        onLoad={onMediaLoaded}
      />
    </div>
  );
}

/** 用户图片容器（支持多图横排） */
function UserImageGallery({
  imageUrls,
  maxWidth,
  onImageClick,
  onMediaLoaded,
}: {
  imageUrls: string[];
  maxWidth: number;
  onImageClick: (index: number) => void;
  onMediaLoaded?: () => void;
}) {
  if (imageUrls.length === 0) return null;

  return (
    <div className="mt-4 flex flex-wrap gap-2">
      {imageUrls.map((url, index) => (
        <UserImage
          key={`${url}-${index}`}
          imageUrl={url}
          index={index}
          maxWidth={maxWidth}
          onImageClick={onImageClick}
          onMediaLoaded={index === 0 ? onMediaLoaded : undefined}
        />
      ))}
    </div>
  );
}

export default function MessageMedia({
  imageUrl,
  videoUrl,
  messageId,
  isUser,
  onImageClick,
  onMediaLoaded,
  isGenerating = false,
  generatingType = 'image',
  imageAspectRatio = '1:1',
  videoAspectRatio = 'landscape',
}: MessageMediaProps) {
  // 解析图片 URL（支持逗号分隔的多图）
  const imageUrls = useMemo(() => parseImageUrls(imageUrl), [imageUrl]);

  // AI 生成图片的占位符尺寸
  const imagePlaceholderSize = useMemo(
    () => getImagePlaceholderSize(imageAspectRatio),
    [imageAspectRatio]
  );
  const videoPlaceholderSize = useMemo(
    () => getVideoPlaceholderSize(videoAspectRatio),
    [videoAspectRatio]
  );

  // 懒加载（用于视频）
  const { ref: videoLazyRef, inView: videoInView } = useInView({
    triggerOnce: true,
    threshold: 0.1,
    rootMargin: '100px',
  });

  // 视频占位符渲染时触发滚动回调
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

  // 没有媒体内容且不在生成中时不渲染
  if (imageUrls.length === 0 && !videoUrl && !isGenerating) return null;

  // 处理图片点击（兼容旧接口）
  const handleImageClick = (index?: number) => {
    onImageClick(index ?? 0);
  };

  return (
    <>
      {/* 图片渲染 */}
      {(imageUrls.length > 0 || (isGenerating && generatingType === 'image')) && (
        isUser ? (
          // 用户图片：直接显示，无占位符，支持多图横排
          <UserImageGallery
            imageUrls={imageUrls}
            maxWidth={imagePlaceholderSize.width}
            onImageClick={handleImageClick}
            onMediaLoaded={onMediaLoaded}
          />
        ) : (
          // AI 生成图片：占位符 + 淡入效果
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
              // 动态最大宽度需要 CSS 变量
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
    </>
  );
}
