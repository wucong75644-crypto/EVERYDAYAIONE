/**
 * 消息媒体组件
 *
 * 负责渲染消息中的图片和视频内容
 * 支持懒加载、点击预览、下载功能
 * 内置占位符，实现平滑淡入效果
 */

import { useState, useEffect } from 'react';
import { useInView } from 'react-intersection-observer';
import { Loader2, Image as ImageIcon, Video as VideoIcon } from 'lucide-react';
import toast from 'react-hot-toast';

interface MessageMediaProps {
  /** 图片 URL */
  imageUrl?: string | null;
  /** 视频 URL */
  videoUrl?: string | null;
  /** 消息 ID（用于下载文件命名） */
  messageId: string;
  /** 是否为用户消息 */
  isUser: boolean;
  /** 图片点击回调（打开预览） */
  onImageClick: () => void;
  /** 媒体加载完成回调（用于滚动调整） */
  onMediaLoaded?: () => void;
  /** 是否正在生成中（显示占位符） */
  isGenerating?: boolean;
  /** 生成类型（image/video） */
  generatingType?: 'image' | 'video';
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
}: MessageMediaProps) {
  // 图片下载状态
  const [isDownloading, setIsDownloading] = useState(false);
  // 图片加载完成状态
  const [imageLoaded, setImageLoaded] = useState(false);
  // 视频加载完成状态
  const [videoLoaded, setVideoLoaded] = useState(false);

  // 当 imageUrl 变化时，重置加载状态
  useEffect(() => {
    if (imageUrl) {
      setImageLoaded(false);
    }
  }, [imageUrl]);

  // 当 videoUrl 变化时，重置加载状态
  useEffect(() => {
    if (videoUrl) {
      setVideoLoaded(false);
    }
  }, [videoUrl]);

  // 懒加载：监听元素是否进入可视区域
  const { ref: lazyRef, inView } = useInView({
    triggerOnce: true,
    threshold: 0.1,
    rootMargin: '100px',
  });

  // 处理图片下载
  const handleImageDownload = async (e: React.MouseEvent) => {
    e.stopPropagation();
    if (isDownloading || !imageUrl) return;

    setIsDownloading(true);
    try {
      const response = await fetch(imageUrl, {
        mode: 'cors',
        credentials: 'omit',
      });
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

  // 是否显示图片占位符：正在生成图片 或 图片URL存在但未加载完成
  const showImagePlaceholder = (isGenerating && generatingType === 'image') || (imageUrl && !imageLoaded);
  // 是否显示视频占位符：正在生成视频 或 视频URL存在但未加载完成
  const showVideoPlaceholder = (isGenerating && generatingType === 'video') || (videoUrl && !videoLoaded);

  // 没有媒体内容且不在生成中时不渲染
  if (!imageUrl && !videoUrl && !isGenerating) return null;

  return (
    <>
      {/* 图片渲染（含占位符） */}
      {(imageUrl || (isGenerating && generatingType === 'image')) && (
        <div className="mt-4 relative w-[180px]" ref={lazyRef}>
          {/* 占位符 - 生成中或图片加载中显示 */}
          {showImagePlaceholder && (
            <div className="rounded-xl w-[180px] h-[180px] bg-gray-100 dark:bg-gray-700 flex items-center justify-center shadow-sm">
              <ImageIcon className="w-10 h-10 text-gray-300 dark:text-gray-500" />
            </div>
          )}

          {/* 图片 - 加载完成后显示 */}
          {imageUrl && inView && (
            <div
              className={`group cursor-pointer transition-opacity duration-500 ease-out ${
                imageLoaded ? 'opacity-100' : 'opacity-0 absolute inset-0'
              }`}
              onClick={onImageClick}
            >
              <img
                src={imageUrl}
                alt={isUser ? '上传的图片' : '生成的图片'}
                className="rounded-xl w-[180px] shadow-sm"
                onLoad={() => {
                  setImageLoaded(true);
                  onMediaLoaded?.();
                }}
                loading="lazy"
              />
              {/* 底部下载按钮（hover 显示） */}
              <div className="absolute bottom-0 left-0 right-0 flex justify-center py-2 bg-gradient-to-t from-black/50 to-transparent rounded-b-xl opacity-0 group-hover:opacity-100 transition-opacity">
                <button
                  className="flex items-center gap-1 px-3 py-1 text-xs text-white bg-black/40 hover:bg-black/60 rounded-full transition-colors disabled:opacity-60"
                  disabled={isDownloading}
                  onClick={handleImageDownload}
                >
                  {isDownloading ? (
                    <Loader2 className="w-3.5 h-3.5 animate-spin" />
                  ) : (
                    <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4" />
                    </svg>
                  )}
                  <span>{isDownloading ? '下载中' : '下载'}</span>
                </button>
              </div>
            </div>
          )}
        </div>
      )}

      {/* 视频渲染（含占位符） */}
      {(videoUrl || (isGenerating && generatingType === 'video')) && (
        <div className="mt-4 relative w-[320px]" ref={!imageUrl ? lazyRef : undefined}>
          {/* 视频占位符 - 生成中或视频加载中显示 */}
          {showVideoPlaceholder && (
            <div className="rounded-xl w-[320px] h-[180px] bg-gray-100 dark:bg-gray-700 flex items-center justify-center shadow-sm">
              <VideoIcon className="w-10 h-10 text-gray-300 dark:text-gray-500" />
            </div>
          )}

          {/* 视频 - 加载完成后显示 */}
          {videoUrl && inView && (
            <div
              className={`transition-opacity duration-500 ease-out ${
                videoLoaded ? 'opacity-100' : 'opacity-0 absolute inset-0'
              }`}
            >
              <video
                src={videoUrl}
                controls
                className="rounded-xl w-[320px] shadow-sm"
                preload="metadata"
                onLoadedMetadata={() => {
                  setVideoLoaded(true);
                  onMediaLoaded?.();
                }}
              >
                您的浏览器不支持视频播放
              </video>
            </div>
          )}
        </div>
      )}
    </>
  );
}
