/**
 * 消息媒体组件
 *
 * 负责渲染消息中的图片和视频内容
 * 支持懒加载、点击预览、下载功能
 */

import { useState } from 'react';
import { useInView } from 'react-intersection-observer';
import { Loader2 } from 'lucide-react';
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
}

export default function MessageMedia({
  imageUrl,
  videoUrl,
  messageId,
  isUser,
  onImageClick,
  onMediaLoaded,
}: MessageMediaProps) {
  // 图片下载状态
  const [isDownloading, setIsDownloading] = useState(false);

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

  // 处理视频下载
  const handleVideoDownload = () => {
    if (!videoUrl) return;
    const link = document.createElement('a');
    link.href = videoUrl;
    link.download = `video-${messageId}.mp4`;
    link.click();
  };

  // 处理视频播放（新窗口）
  const handleVideoPlay = () => {
    if (videoUrl) {
      window.open(videoUrl, '_blank');
    }
  };

  // 没有媒体内容时不渲染
  if (!imageUrl && !videoUrl) return null;

  return (
    <>
      {/* 图片渲染 */}
      {imageUrl && (
        <div className="mt-4" ref={lazyRef}>
          {inView ? (
            <div
              className="relative group w-fit cursor-pointer"
              onClick={onImageClick}
            >
              <img
                src={imageUrl}
                alt={isUser ? '上传的图片' : '生成的图片'}
                className="rounded-xl w-full max-w-[240px] shadow-sm"
                onLoad={onMediaLoaded}
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
          ) : (
            <div className="rounded-xl w-full max-w-[240px] aspect-[4/3] bg-gray-100 flex items-center justify-center">
              <svg className="w-8 h-8 text-gray-400 animate-pulse" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 16l4.586-4.586a2 2 0 012.828 0L16 16m-2-2l1.586-1.586a2 2 0 012.828 0L20 14m-6-6h.01M6 20h12a2 2 0 002-2V6a2 2 0 00-2 2v12a2 2 0 002 2z" />
              </svg>
            </div>
          )}
        </div>
      )}

      {/* 视频渲染 */}
      {videoUrl && (
        <div className="mt-4" ref={!imageUrl ? lazyRef : undefined}>
          {(!imageUrl && !inView) ? (
            <div className="rounded-xl w-full max-w-[400px] aspect-video bg-gray-100 flex items-center justify-center">
              <svg className="w-8 h-8 text-gray-400 animate-pulse" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M14.752 11.168l-3.197-2.132A1 1 0 0010 9.87v4.263a1 1 0 001.555.832l3.197-2.132a1 1 0 000-1.664z" />
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
              </svg>
            </div>
          ) : (
            <video
              src={videoUrl}
              controls
              className="rounded-xl w-full max-w-[400px] shadow-sm"
              preload="metadata"
              onLoadedMetadata={onMediaLoaded}
            >
              您的浏览器不支持视频播放
            </video>
          )}

          {/* 视频操作按钮（仅 AI 消息显示） */}
          {!isUser && (
            <div className="flex items-center gap-2 mt-3 pt-3 border-t border-gray-100">
              <button
                className="text-xs text-gray-600 hover:text-gray-900 hover:bg-gray-50 px-2 py-1 rounded-lg flex items-center gap-1 transition-colors"
                onClick={handleVideoPlay}
              >
                <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M14.752 11.168l-3.197-2.132A1 1 0 0010 9.87v4.263a1 1 0 001.555.832l3.197-2.132a1 1 0 000-1.664z" />
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
                </svg>
                <span>播放</span>
              </button>
              <button
                className="text-xs text-gray-600 hover:text-gray-900 hover:bg-gray-50 px-2 py-1 rounded-lg flex items-center gap-1 transition-colors"
                onClick={handleVideoDownload}
              >
                <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4" />
                </svg>
                <span>下载</span>
              </button>
            </div>
          )}
        </div>
      )}
    </>
  );
}
