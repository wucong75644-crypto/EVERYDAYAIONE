/**
 * 单条消息组件
 *
 * 支持用户消息和 AI 消息的不同样式
 * 组合 MessageMedia 和 MessageActions 子组件
 */

import { memo, useState, useRef, useEffect, useMemo } from 'react';
import type { Message } from '../../services/message';
import DeleteMessageModal from './DeleteMessageModal';
import ImagePreviewModal from './ImagePreviewModal';
import MessageMedia from './MessageMedia';
import MessageActions from './MessageActions';

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

export default memo(function MessageItem({
  message,
  isStreaming = false,
  isRegenerating = false,
  onRegenerate,
  onDelete,
  onMediaLoaded,
}: MessageItemProps) {
  const isUser = message.role === 'user';

  // 判断是否为失败消息：只检查 is_error 标志
  const isErrorMessage = message.is_error === true;

  // 判断是否为媒体占位符消息（图片/视频生成中）
  const mediaPlaceholderInfo = useMemo(() => {
    // 条件：streaming- 开头 + AI消息 + 特定文本 + 无媒体
    if (
      !message.id.startsWith('streaming-') ||
      message.role !== 'assistant' ||
      message.image_url ||
      message.video_url
    ) {
      return null;
    }

    if (message.content.includes('图片生成中')) {
      return { type: 'image' as const, text: message.content };
    }
    if (message.content.includes('视频生成中')) {
      return { type: 'video' as const, text: message.content };
    }
    return null;
  }, [message.id, message.role, message.content, message.image_url, message.video_url]);

  // 工具栏显示/隐藏状态
  const [showToolbar, setShowToolbar] = useState(false);
  const hideTimeoutRef = useRef<number | null>(null);
  const isMouseOnToolbarRef = useRef(false);

  // 删除确认弹框状态
  const [showDeleteModal, setShowDeleteModal] = useState(false);
  const [isDeleting, setIsDeleting] = useState(false);

  // 图片预览弹窗状态
  const [showImagePreview, setShowImagePreview] = useState(false);

  // 鼠标进入消息区域 - 显示工具栏并清除隐藏定时器
  const handleMouseEnter = () => {
    if (hideTimeoutRef.current) {
      clearTimeout(hideTimeoutRef.current);
      hideTimeoutRef.current = null;
    }
    setShowToolbar(true);
  };

  // 鼠标离开消息区域 - 延迟 1.5 秒隐藏工具栏
  const handleMouseLeave = () => {
    if (isMouseOnToolbarRef.current) return;

    hideTimeoutRef.current = window.setTimeout(() => {
      if (!isMouseOnToolbarRef.current) {
        setShowToolbar(false);
      }
    }, 1500);
  };

  // 工具栏鼠标进入
  const handleToolbarMouseEnter = () => {
    isMouseOnToolbarRef.current = true;
    if (hideTimeoutRef.current) {
      clearTimeout(hideTimeoutRef.current);
      hideTimeoutRef.current = null;
    }
    setShowToolbar(true);
  };

  // 工具栏鼠标离开
  const handleToolbarMouseLeave = () => {
    isMouseOnToolbarRef.current = false;
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

  return (
    <div className={`flex mb-12 ${isUser ? 'justify-end' : 'justify-start'}`}>
      <div
        className="relative max-w-[80%]"
        onMouseEnter={handleMouseEnter}
        onMouseLeave={handleMouseLeave}
      >
        {/* 消息气泡 */}
        <div
          className={`rounded-2xl px-5 py-3 ${
            isUser
              ? 'bg-gradient-to-r from-purple-500 to-indigo-500 text-white'
              : 'bg-white border border-gray-200 text-gray-900'
          }`}
        >
          {/* 消息文本 */}
          <div className="text-[15px] leading-relaxed whitespace-pre-wrap">
            {/* 加载状态：重新生成或流式输出开始但内容为空 */}
            {((isRegenerating || isStreaming) && !message.content) ? (
              <div className="flex items-center space-x-2 text-gray-500">
                <div className="flex space-x-1">
                  <span className="w-2 h-2 bg-gray-400 rounded-full animate-bounce" style={{ animationDelay: '0ms' }}></span>
                  <span className="w-2 h-2 bg-gray-400 rounded-full animate-bounce" style={{ animationDelay: '150ms' }}></span>
                  <span className="w-2 h-2 bg-gray-400 rounded-full animate-bounce" style={{ animationDelay: '300ms' }}></span>
                </div>
                <span className="text-sm">{isRegenerating ? '正在重新生成...' : 'AI 正在思考...'}</span>
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

          {/* 媒体内容（含占位符） */}
          <MessageMedia
            imageUrl={message.image_url}
            videoUrl={message.video_url}
            messageId={message.id}
            isUser={isUser}
            onImageClick={() => setShowImagePreview(true)}
            onMediaLoaded={onMediaLoaded}
            isGenerating={!!mediaPlaceholderInfo}
            generatingType={mediaPlaceholderInfo?.type}
          />
        </div>

        {/* 操作工具栏 */}
        <MessageActions
          messageId={message.id}
          content={message.content}
          isUser={isUser}
          isErrorMessage={isErrorMessage}
          isRegenerating={isRegenerating}
          visible={showToolbar}
          onRegenerate={onRegenerate}
          onDeleteClick={onDelete ? () => setShowDeleteModal(true) : undefined}
          onMouseEnter={handleToolbarMouseEnter}
          onMouseLeave={handleToolbarMouseLeave}
        />
      </div>

      {/* 删除确认弹框 */}
      <DeleteMessageModal
        isOpen={showDeleteModal}
        onClose={() => setShowDeleteModal(false)}
        onConfirm={handleDeleteConfirm}
        loading={isDeleting}
      />

      {/* 图片预览弹窗 */}
      {showImagePreview && message.image_url && (
        <ImagePreviewModal
          imageUrl={message.image_url}
          onClose={() => setShowImagePreview(false)}
          filename={`image-${message.id}`}
        />
      )}
    </div>
  );
});
