/**
 * 单条消息组件
 *
 * 支持用户消息和 AI 消息的不同样式
 */

import { memo, useState, useRef, useEffect } from 'react';
import { useInView } from 'react-intersection-observer';
import { Trash2 } from 'lucide-react';
import type { Message } from '../../services/message';
import DeleteMessageModal from './DeleteMessageModal';

interface MessageItemProps {
  message: Message;
  /** 是否正在流式输出 */
  isStreaming?: boolean;
  /** 是否正在重新生成 */
  isRegenerating?: boolean;
  /** 重新生成回调 */
  onRegenerate?: (messageId: string) => void;
  /** 删除回调 */
  onDelete?: (messageId: string) => void;
  /** 媒体加载完成回调（用于滚动调整） */
  onMediaLoaded?: () => void;
}

export default memo(function MessageItem({ message, isStreaming = false, isRegenerating = false, onRegenerate, onDelete, onMediaLoaded }: MessageItemProps) {
  const isUser = message.role === 'user';

  // 判断是否为失败消息：只检查 is_error 标志（避免误判正常消息）
  const isErrorMessage = message.is_error === true;

  const [copied, setCopied] = useState(false);

  // 工具栏显示/隐藏状态
  const [showToolbar, setShowToolbar] = useState(false);
  const hideTimeoutRef = useRef<number | null>(null);
  const isMouseOnToolbarRef = useRef(false);

  // "更多"菜单显示/隐藏状态
  const [showMoreMenu, setShowMoreMenu] = useState(false);
  const moreMenuRef = useRef<HTMLDivElement>(null);

  // 删除确认弹框状态
  const [showDeleteModal, setShowDeleteModal] = useState(false);
  const [isDeleting, setIsDeleting] = useState(false);

  // 鼠标进入消息区域 - 显示工具栏并清除隐藏定时器
  const handleMouseEnter = () => {
    if (hideTimeoutRef.current) {
      clearTimeout(hideTimeoutRef.current);
      hideTimeoutRef.current = null;
    }
    setShowToolbar(true);
  };

  // 鼠标离开消息区域 - 延迟1.5秒隐藏工具栏
  const handleMouseLeave = () => {
    // 如果菜单打开或鼠标在工具栏上，不隐藏工具栏
    if (showMoreMenu || isMouseOnToolbarRef.current) return;

    hideTimeoutRef.current = window.setTimeout(() => {
      // 再次检查鼠标是否还在工具栏上
      if (!isMouseOnToolbarRef.current) {
        setShowToolbar(false);
      }
    }, 1500);
  };

  // 工具栏鼠标进入 - 标记鼠标在工具栏上，清除隐藏定时器
  const handleToolbarMouseEnter = () => {
    isMouseOnToolbarRef.current = true;
    if (hideTimeoutRef.current) {
      clearTimeout(hideTimeoutRef.current);
      hideTimeoutRef.current = null;
    }
    setShowToolbar(true);
  };

  // 工具栏鼠标离开 - 标记鼠标不在工具栏上，延迟隐藏
  const handleToolbarMouseLeave = () => {
    isMouseOnToolbarRef.current = false;
    // 如果菜单打开，不隐藏工具栏
    if (showMoreMenu) return;

    hideTimeoutRef.current = window.setTimeout(() => {
      setShowToolbar(false);
    }, 1500);
  };

  // 组件卸载时清理定时器
  useEffect(() => {
    return () => {
      if (hideTimeoutRef.current) {
        clearTimeout(hideTimeoutRef.current);
      }
    };
  }, []);

  // 点击外部关闭"更多"菜单
  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (moreMenuRef.current && !moreMenuRef.current.contains(event.target as Node)) {
        setShowMoreMenu(false);
        // 关闭菜单后，如果鼠标不在工具栏上，则延迟隐藏工具栏
        if (!isMouseOnToolbarRef.current) {
          hideTimeoutRef.current = window.setTimeout(() => {
            setShowToolbar(false);
          }, 1500);
        }
      }
    };

    if (showMoreMenu) {
      document.addEventListener('mousedown', handleClickOutside);
      return () => document.removeEventListener('mousedown', handleClickOutside);
    }
  }, [showMoreMenu]);

  // 处理删除确认
  const handleDeleteConfirm = async () => {
    if (!onDelete) return;

    try {
      setIsDeleting(true);
      await onDelete(message.id);
      setShowDeleteModal(false);
    } catch (error) {
      console.error('删除消息失败:', error);
    } finally {
      setIsDeleting(false);
    }
  };

  // 懒加载：监听元素是否进入可视区域
  const { ref: lazyRef, inView } = useInView({
    triggerOnce: true, // 只触发一次
    threshold: 0.1, // 10% 进入可视区域就触发
    rootMargin: '100px', // 提前 100px 开始加载
  });

  // 复制功能
  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(message.content);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch (error) {
      console.error('复制失败:', error);
    }
  };

  // 朗读功能
  const handleSpeak = () => {
    // 暂不支持
  };

  // 点赞/点踩功能
  const handleFeedback = (_type: 'like' | 'dislike') => {
    // 暂不支持
  };

  // 分享功能
  const handleShare = async () => {
    if (navigator.share) {
      try {
        await navigator.share({
          title: '分享消息',
          text: message.content,
        });
      } catch (error) {
        console.log('分享取消或失败:', error);
      }
    } else {
      // 降级方案：复制到剪贴板
      handleCopy();
    }
  };

  return (
    <div className={`flex mb-12 ${isUser ? 'justify-end' : 'justify-start'}`}>
      <div
        className="relative max-w-[80%]"
        onMouseEnter={handleMouseEnter}
        onMouseLeave={handleMouseLeave}
      >
        <div
          className={`rounded-2xl px-5 py-3 ${
            isUser
              ? 'bg-gradient-to-r from-purple-500 to-indigo-500 text-white'
              : 'bg-white border border-gray-200 text-gray-900'
          }`}
        >
          {/* 消息文本 */}
          <div className="text-[15px] leading-relaxed whitespace-pre-wrap">
            {/* 重新生成加载状态：当正在重新生成且内容为空时显示 */}
            {isRegenerating && !message.content ? (
              <div className="flex items-center space-x-2 text-gray-500">
                <div className="flex space-x-1">
                  <span className="w-2 h-2 bg-gray-400 rounded-full animate-bounce" style={{ animationDelay: '0ms' }}></span>
                  <span className="w-2 h-2 bg-gray-400 rounded-full animate-bounce" style={{ animationDelay: '150ms' }}></span>
                  <span className="w-2 h-2 bg-gray-400 rounded-full animate-bounce" style={{ animationDelay: '300ms' }}></span>
                </div>
                <span className="text-sm">正在重新生成...</span>
              </div>
            ) : (
              <>
                {message.content}
                {/* 流式输出光标 */}
                {(isStreaming || isRegenerating) && message.content && (
                  <span className="inline-block w-2 h-4 bg-blue-500 ml-0.5 animate-pulse" />
                )}
              </>
            )}
          </div>

          {/* 移动端重试链接（仅错误消息显示） */}
          {!isUser && isErrorMessage && (
            <div className="mt-2 sm:hidden">
              <button
                onClick={() => onRegenerate?.(message.id)}
                disabled={isRegenerating}
                className="text-sm text-blue-600 hover:text-blue-700 disabled:text-gray-400 disabled:cursor-not-allowed"
              >
                服务不可用。<span className="underline">[重试]</span>
              </button>
            </div>
          )}

          {/* 图片（如果有） */}
          {message.image_url && (
            <div className="mt-4" ref={lazyRef}>
              {inView ? (
                <img
                  src={message.image_url}
                  alt={isUser ? '上传的图片' : '生成的图片'}
                  className="rounded-xl w-full max-w-[240px] cursor-pointer hover:opacity-95 transition-opacity shadow-sm"
                  onClick={() => {
                    window.open(message.image_url!, '_blank');
                  }}
                  onLoad={onMediaLoaded}
                  loading="lazy"
                />
              ) : (
                <div className="rounded-xl w-full max-w-[240px] h-[240px] bg-gray-100 flex items-center justify-center">
                  <svg className="w-8 h-8 text-gray-400 animate-pulse" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 16l4.586-4.586a2 2 0 012.828 0L16 16m-2-2l1.586-1.586a2 2 0 012.828 0L20 14m-6-6h.01M6 20h12a2 2 0 002-2V6a2 2 0 00-2-2H6a2 2 0 00-2 2v12a2 2 0 002 2z" />
                  </svg>
                </div>
              )}
              {/* 图片操作按钮 */}
              {!isUser && (
                <div className="flex items-center gap-2 mt-3 pt-3 border-t border-gray-100">
                  <button
                    className="text-xs text-gray-600 hover:text-gray-900 hover:bg-gray-50 px-2 py-1 rounded-lg flex items-center gap-1 transition-colors"
                    onClick={() => window.open(message.image_url!, '_blank')}
                  >
                    <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0zM10 7v3m0 0v3m0-3h3m-3 0H7" />
                    </svg>
                    <span>查看</span>
                  </button>
                  <button
                    className="text-xs text-gray-600 hover:text-gray-900 hover:bg-gray-50 px-2 py-1 rounded-lg flex items-center gap-1 transition-colors"
                    onClick={() => {
                      const link = document.createElement('a');
                      link.href = message.image_url!;
                      link.download = `image-${message.id}.png`;
                      link.click();
                    }}
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

          {/* 视频（如果有） */}
          {message.video_url && (
            <div className="mt-4" ref={!message.image_url ? lazyRef : undefined}>
              {(!message.image_url && !inView) ? (
                <div className="rounded-xl w-full max-w-[400px] h-[225px] bg-gray-100 flex items-center justify-center">
                  <svg className="w-8 h-8 text-gray-400 animate-pulse" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M14.752 11.168l-3.197-2.132A1 1 0 0010 9.87v4.263a1 1 0 001.555.832l3.197-2.132a1 1 0 000-1.664z" />
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
                  </svg>
                </div>
              ) : (
                <video
                  src={message.video_url}
                  controls
                  className="rounded-xl w-full max-w-[400px] shadow-sm"
                  preload="metadata"
                  onLoadedMetadata={onMediaLoaded}
                >
                  您的浏览器不支持视频播放
                </video>
              )}
              {/* 视频操作按钮 */}
              {!isUser && (
                <div className="flex items-center gap-2 mt-3 pt-3 border-t border-gray-100">
                  <button
                    className="text-xs text-gray-600 hover:text-gray-900 hover:bg-gray-50 px-2 py-1 rounded-lg flex items-center gap-1 transition-colors"
                    onClick={() => window.open(message.video_url!, '_blank')}
                  >
                    <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M14.752 11.168l-3.197-2.132A1 1 0 0010 9.87v4.263a1 1 0 001.555.832l3.197-2.132a1 1 0 000-1.664z" />
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
                    </svg>
                    <span>播放</span>
                  </button>
                  <button
                    className="text-xs text-gray-600 hover:text-gray-900 hover:bg-gray-50 px-2 py-1 rounded-lg flex items-center gap-1 transition-colors"
                    onClick={() => {
                      const link = document.createElement('a');
                      link.href = message.video_url!;
                      link.download = `video-${message.id}.mp4`;
                      link.click();
                    }}
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
        </div>

        {/* 悬停显示的功能按钮 */}
        <div
          className={`absolute bottom-0 ${
            isUser ? 'right-0' : 'left-0'
          } transform translate-y-full pt-1 flex items-center gap-1 transition-opacity duration-300 ${
            showToolbar ? 'opacity-100' : 'opacity-0 pointer-events-none'
          }`}
          onMouseEnter={handleToolbarMouseEnter}
          onMouseLeave={handleToolbarMouseLeave}
        >
            {/* 复制按钮 */}
            <button
              onClick={handleCopy}
              className="p-1.5 text-gray-500 hover:text-gray-700 hover:bg-gray-100 rounded-lg transition-colors"
              title={copied ? '已复制' : '复制'}
            >
              {copied ? (
                <svg className="w-4 h-4 text-green-600" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                </svg>
              ) : (
                <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z" />
                </svg>
              )}
            </button>

            {/* 朗读按钮 */}
            <button
              onClick={handleSpeak}
              className="p-1.5 text-gray-500 hover:text-gray-700 hover:bg-gray-100 rounded-lg transition-colors"
              title="朗读"
            >
              <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15.536 8.464a5 5 0 010 7.072m2.828-9.9a9 9 0 010 12.728M5.586 15H4a1 1 0 01-1-1v-4a1 1 0 011-1h1.586l4.707-4.707C10.923 3.663 12 4.109 12 5v14c0 .891-1.077 1.337-1.707.707L5.586 15z" />
              </svg>
            </button>

            {/* AI 消息才显示反馈按钮 */}
            {!isUser && (
              <>
                {/* 点赞按钮 */}
                <button
                  onClick={() => handleFeedback('like')}
                  className="p-1.5 text-gray-500 hover:text-green-600 hover:bg-gray-100 rounded-lg transition-colors"
                  title="有帮助"
                >
                  <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M14 10h4.764a2 2 0 011.789 2.894l-3.5 7A2 2 0 0115.263 21h-4.017c-.163 0-.326-.02-.485-.06L7 20m7-10V5a2 2 0 00-2-2h-.095c-.5 0-.905.405-.905.905 0 .714-.211 1.412-.608 2.006L7 11v9m7-10h-2M7 20H5a2 2 0 01-2-2v-6a2 2 0 012-2h2.5" />
                  </svg>
                </button>

                {/* 点踩按钮 */}
                <button
                  onClick={() => handleFeedback('dislike')}
                  className="p-1.5 text-gray-500 hover:text-red-600 hover:bg-gray-100 rounded-lg transition-colors"
                  title="没有帮助"
                >
                  <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10 14H5.236a2 2 0 01-1.789-2.894l3.5-7A2 2 0 018.736 3h4.018a2 2 0 01.485.06l3.76.94m-7 10v5a2 2 0 002 2h.096c.5 0 .905-.405.905-.904 0-.715.211-1.413.608-2.008L17 13V4m-7 10h2m5-10h2a2 2 0 012 2v6a2 2 0 01-2 2h-2.5" />
                  </svg>
                </button>
              </>
            )}

            {/* 重新生成/重试按钮（所有AI消息显示）- 移到分享按钮之前 */}
            {!isUser && onRegenerate && (
              <button
                onClick={() => onRegenerate(message.id)}
                disabled={isRegenerating}
                className="p-1.5 text-gray-500 hover:text-blue-600 hover:bg-gray-100 rounded-lg transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                title={isRegenerating ? '处理中...' : isErrorMessage ? '重试' : '重新生成'}
              >
                {isRegenerating ? (
                  <svg className="w-4 h-4 animate-spin" fill="none" viewBox="0 0 24 24">
                    <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                    <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
                  </svg>
                ) : (
                  <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
                  </svg>
                )}
              </button>
            )}

            {/* 分享按钮 */}
            <button
              onClick={handleShare}
              className="p-1.5 text-gray-500 hover:text-gray-700 hover:bg-gray-100 rounded-lg transition-colors"
              title="分享"
            >
              <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8.684 13.342C8.886 12.938 9 12.482 9 12c0-.482-.114-.938-.316-1.342m0 2.684a3 3 0 110-2.684m0 2.684l6.632 3.316m-6.632-6l6.632-3.316m0 0a3 3 0 105.367-2.684 3 3 0 00-5.367 2.684zm0 9.316a3 3 0 105.368 2.684 3 3 0 00-5.368-2.684z" />
              </svg>
            </button>

            {/* 更多按钮（包含下拉菜单） */}
            <div className="relative" ref={moreMenuRef}>
              <button
                onClick={() => setShowMoreMenu(!showMoreMenu)}
                className={`p-1.5 rounded-lg transition-all duration-150 ${
                  showMoreMenu
                    ? 'text-gray-900 bg-gray-200'
                    : 'text-gray-500 hover:text-gray-700 hover:bg-gray-100'
                }`}
                title="更多"
              >
                <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 5v.01M12 12v.01M12 19v.01M12 6a1 1 0 110-2 1 1 0 010 2zm0 7a1 1 0 110-2 1 1 0 010 2zm0 7a1 1 0 110-2 1 1 0 010 2z" />
                </svg>
              </button>

              {/* 下拉菜单 */}
              {showMoreMenu && (
                <div className="absolute bottom-full right-0 mb-1.5 bg-white rounded-lg shadow-lg border border-gray-200 py-1 min-w-[100px] z-10 animate-in fade-in zoom-in-95 duration-100">
                  {onDelete && (
                    <button
                      onClick={() => {
                        setShowMoreMenu(false);
                        setShowDeleteModal(true);
                      }}
                      className="w-full px-3 py-1.5 text-left text-xs text-red-600 hover:bg-red-50 flex items-center gap-2 transition-colors"
                    >
                      <Trash2 className="w-3.5 h-3.5 flex-shrink-0" />
                      <span>删除</span>
                    </button>
                  )}
                </div>
              )}
            </div>
          </div>
      </div>

      {/* 删除确认弹框 */}
      <DeleteMessageModal
        isOpen={showDeleteModal}
        onClose={() => setShowDeleteModal(false)}
        onConfirm={handleDeleteConfirm}
        loading={isDeleting}
      />
    </div>
  );
});
