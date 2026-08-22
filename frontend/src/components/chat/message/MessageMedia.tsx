/** 消息媒体组件 - 渲染消息中的图片和视频内容 */

import { memo, useEffect, useMemo, useCallback, useRef } from 'react';
import { useInView } from 'react-intersection-observer';
import { type AspectRatio, type VideoAspectRatio } from '../../../constants/models';
import { getImagePlaceholderSize, getVideoPlaceholderSize } from '../../../utils/settingsStorage';
import MediaPlaceholder, { FailedMediaPlaceholder } from '../media/MediaPlaceholder';
import AiImageGrid from '../media/AiImageGrid';
import styles from '../menus/shared.module.css';
import type { ContentPart, FilePart, ImageAsset } from '../../../types/message';
import FileCardList from '../media/FileCard';
import { AiGeneratedImage, UserImage, UserImageGallery } from './MessageImageBlocks';
import { resolveImageOriginalUrl } from '../../../utils/messageUtils';

interface MessageMediaProps {
  /** 图片资产列表（原图/缩略图分离） */
  imageAssets?: ImageAsset[];
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

type OrderedUserMediaRun =
  | {
    kind: 'visuals';
    items: Array<
      | { kind: 'image'; asset: ImageAsset; imageIndex: number }
      | { kind: 'video'; url: string }
    >;
  }
  | { kind: 'files'; files: FilePart[] }

function buildOrderedUserMediaRuns(
  content: ContentPart[],
  imageAssets: ImageAsset[],
): OrderedUserMediaRun[] {
  const imageIndicesByUrl = new Map<string, number[]>();
  imageAssets.forEach((asset, index) => {
    const indices = imageIndicesByUrl.get(asset.originalUrl) || [];
    indices.push(index);
    imageIndicesByUrl.set(asset.originalUrl, indices);
  });

  const runs: OrderedUserMediaRun[] = [];
  for (const part of content) {
    if (part.type === 'image') {
      const originalUrl = resolveImageOriginalUrl(part);
      if (!originalUrl) continue;
      const indices = imageIndicesByUrl.get(originalUrl);
      const imageIndex = indices?.shift();
      if (imageIndex === undefined) continue;
      const asset = imageAssets[imageIndex];
      const last = runs[runs.length - 1];
      if (last?.kind === 'visuals') {
        last.items.push({ kind: 'image', asset, imageIndex });
      } else {
        runs.push({ kind: 'visuals', items: [{ kind: 'image', asset, imageIndex }] });
      }
      continue;
    }
    if (part.type === 'file') {
      if (!part.url && !part.workspace_path) continue;
      const last = runs[runs.length - 1];
      if (last?.kind === 'files') last.files.push(part);
      else runs.push({ kind: 'files', files: [part] });
      continue;
    }
    if (part.type === 'video' && part.url) {
      const last = runs[runs.length - 1];
      if (last?.kind === 'visuals') last.items.push({ kind: 'video', url: part.url });
      else runs.push({ kind: 'visuals', items: [{ kind: 'video', url: part.url }] });
    }
  }
  return runs;
}

function OrderedUserMedia({
  content = [],
  imageAssets = [],
  imageWidth,
  imageHeight,
  messageId,
  onImageClick,
  onMediaLoaded,
}: Pick<MessageMediaProps, 'content' | 'imageAssets' | 'messageId' | 'onImageClick' | 'onMediaLoaded'> & {
  imageWidth: number;
  imageHeight: number;
}) {
  const runs = useMemo(
    () => buildOrderedUserMediaRuns(content, imageAssets),
    [content, imageAssets],
  );

  if (runs.length === 0) return null;

  return (
    <div className="flex w-[min(720px,90vw)] max-w-full flex-col items-end gap-2">
      {runs.map((run, runIndex) => {
        if (run.kind === 'visuals') {
          const tileWidth = run.items.length > 1 ? Math.min(imageWidth, 280) : imageWidth;
          const tileHeight = run.items.length > 1
            ? Math.round(tileWidth * imageHeight / imageWidth)
            : imageHeight;
          return (
            <div
              key={`visuals-${runIndex}`}
              data-attachment-run="visuals"
              className="mt-4 grid w-full justify-end gap-2"
              style={{ gridTemplateColumns: `repeat(auto-fit, ${tileWidth}px)` }}
            >
              {run.items.map((item, itemIndex) => item.kind === 'image' ? (
                <UserImage
                  key={`${item.asset.originalUrl}-${itemIndex}`}
                  imageAsset={item.asset}
                  index={itemIndex}
                  messageId={messageId}
                  onImageClick={() => onImageClick(item.imageIndex)}
                  onMediaLoaded={itemIndex === 0 && runIndex === 0 ? onMediaLoaded : undefined}
                  displayWidth={tileWidth}
                  displayHeight={tileHeight}
                />
              ) : (
                <video
                  key={`video-${itemIndex}`}
                  src={item.url}
                  controls
                  className="block rounded-xl shadow-sm object-contain"
                  style={{ width: tileWidth, height: tileHeight }}
                  preload="metadata"
                  onLoadedMetadata={itemIndex === 0 && runIndex === 0 ? onMediaLoaded : undefined}
                >
                  您的浏览器不支持视频播放
                </video>
              ))}
            </div>
          );
        }
        if (run.kind === 'files') {
          return (
            <div key={`files-${runIndex}`} data-attachment-run="files" className="w-full max-w-[590px] self-end">
              <FileCardList files={run.files} />
            </div>
          );
        }
      })}
    </div>
  );
}

export default memo(function MessageMedia({
  imageAssets = [],
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
  const failedImage = useMemo(() => {
    const failedPart = content.find((part) => part.type === 'image' && part.failed);
    return failedPart?.type === 'image' ? failedPart : undefined;
  }, [content]);

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
  const hasOrderedUserMedia = isUser && content.some((part) => (
    part.type === 'image' || part.type === 'file' || part.type === 'video'
  ));
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

  if (imageAssets.length === 0 && !videoUrl && !isGenerating && !failedMediaType && files.length === 0
    && !hasOrderedUserMedia) return null;

  if (hasOrderedUserMedia) {
    return (
      <OrderedUserMedia
        content={content}
        imageAssets={imageAssets}
        imageWidth={imagePlaceholderSize.width}
        imageHeight={imagePlaceholderSize.height}
        messageId={messageId}
        onImageClick={handleImageClick}
        onMediaLoaded={onMediaLoaded}
      />
    );
  }

  return (
    <>
      {/* 图片渲染 */}
      {(imageAssets.length > 0
        || (isGenerating && generatingType === 'image')
        || (failedMediaType === 'image' && numImages > 1)) && (
        isUser ? (
          // 用户图片：直接显示，无占位符，支持多图横排
          <UserImageGallery
            imageAssets={imageAssets}
            messageId={messageId}
            maxWidth={imagePlaceholderSize.width}
            maxHeight={imagePlaceholderSize.height}
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
            imageAsset={imageAssets[0] || null}
            messageId={messageId}
            placeholderSize={imagePlaceholderSize}
            onImageClick={() => handleImageClick(0)}
            onMediaLoaded={onMediaLoaded}
            isGenerating={isGenerating && generatingType === 'image'}
          />
        )
      )}

      {/* 失败的图片占位符（裂开状态 + hover 重新生成） */}
      {failedMediaType === 'image' && numImages === 1 && imageAssets.length === 0 && !isGenerating && (
        <div className="mt-3">
          <FailedMediaPlaceholder
            type="image"
            width={imagePlaceholderSize.width}
            height={imagePlaceholderSize.height}
            onRetry={onRegenerate}
            errorMessage={failedImage?.error || '图片生成失败'}
            errorCode={failedImage?.error_code}
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
