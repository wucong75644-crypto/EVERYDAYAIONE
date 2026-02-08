/**
 * 单条消息组件
 *
 * 支持用户消息和 AI 消息的不同样式
 * 组合 MessageMedia 和 MessageActions 子组件
 */

import { memo, useState, useRef, useEffect, useMemo, useCallback } from 'react';
import type { Message } from '../../stores/useMessageStore';
import { getTextContent, getImageUrls, getVideoUrls } from '../../stores/useMessageStore';
import DeleteMessageModal from './DeleteMessageModal';
import ImagePreviewModal from './ImagePreviewModal';
import MessageMedia from './MessageMedia';
import MessageActions from './MessageActions';
import { getSavedSettings } from '../../utils/settingsStorage';
import { useModalAnimation } from '../../hooks/useModalAnimation';
import LoadingPlaceholder from './LoadingPlaceholder';
import { PLACEHOLDER_TEXT } from '../../constants/placeholder';
import type { AspectRatio, VideoAspectRatio } from '../../constants/models';

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
  /** 所有图片 URL 列表（用于缩略图预览） */
  allImageUrls?: string[];
  /** 当前图片在列表中的索引（用于缩略图预览） */
  currentImageIndex?: number;
}

export default memo(function MessageItem({
  message,
  isStreaming = false,
  isRegenerating = false,
  onRegenerate,
  onDelete,
  onMediaLoaded,
  allImageUrls = [],
  currentImageIndex = 0,
}: MessageItemProps) {
  const isUser = message.role === 'user';

  // 提取内容（兼容新旧格式）
  const textContent = getTextContent(message);
  const imageUrls = getImageUrls(message);
  const videoUrls = getVideoUrls(message);
  const hasImage = imageUrls.length > 0;
  const hasVideo = videoUrls.length > 0;

  // 判断是否为失败消息
  const isErrorMessage = message.status === 'failed' || message.is_error === true;

  // 获取当前高级设置（用于占位符动态尺寸）
  const savedSettings = useMemo(() => getSavedSettings(), []);

  // 计算实际使用的宽高比：已生成的媒体使用保存的参数，生成中使用当前设置
  const genParams = message.generation_params || {};
  const actualImageAspectRatio = (genParams.aspect_ratio ?? genParams.aspectRatio ?? savedSettings.image.aspectRatio) as AspectRatio;
  const actualVideoAspectRatio = (genParams.aspect_ratio ?? genParams.aspectRatio ?? savedSettings.video.aspectRatio) as VideoAspectRatio;

  // 判断是否为媒体占位符消息（图片/视频生成中）
  const mediaPlaceholderInfo = useMemo(() => {
    // 条件：pending 状态 + AI消息 + 无媒体
    if (
      message.role !== 'assistant' ||
      hasImage ||
      hasVideo
    ) {
      return null;
    }

    // 检查是否为 pending 状态的媒体生成
    if (message.status === 'pending') {
      const genType = message.generation_params?.type;
      if (genType === 'image') {
        return { type: 'image' as const, text: PLACEHOLDER_TEXT.IMAGE_GENERATING };
      }
      if (genType === 'video') {
        return { type: 'video' as const, text: PLACEHOLDER_TEXT.VIDEO_GENERATING };
      }
    }

    // 兼容旧格式：检查文本内容
    if (textContent.includes(PLACEHOLDER_TEXT.IMAGE_GENERATING)) {
      return { type: 'image' as const, text: PLACEHOLDER_TEXT.IMAGE_GENERATING };
    }
    if (textContent.includes(PLACEHOLDER_TEXT.VIDEO_GENERATING)) {
      return { type: 'video' as const, text: PLACEHOLDER_TEXT.VIDEO_GENERATING };
    }
    return null;
  }, [message.role, message.status, message.generation_params, hasImage, hasVideo, textContent]);

  // 工具栏显示/隐藏状态
  const [showToolbar, setShowToolbar] = useState(false);
  const hideTimeoutRef = useRef<number | null>(null);
  const isMouseOnToolbarRef = useRef(false);

  // 使用自定义 Hook 管理删除弹框动画
  const {
    isOpen: showDeleteModal,
    isClosing: deleteModalClosing,
    open: openDeleteModal,
    close: closeDeleteModal,
  } = useModalAnimation();
  const [isDeleting, setIsDeleting] = useState(false);

  // 图片预览弹窗状态
  const [showImagePreview, setShowImagePreview] = useState(false);
  const [previewIndex, setPreviewIndex] = useState(currentImageIndex);

  // 用于预览的图片列表：所有消息都使用全局列表（支持查看对话中所有图片）
  const previewImageUrls = allImageUrls;

  // 将消息内图片索引转换为全局索引
  const getGlobalImageIndex = useCallback((localIndex: number): number => {
    if (imageUrls.length === 0 || localIndex >= imageUrls.length) {
      return currentImageIndex;
    }
    const targetUrl = imageUrls[localIndex];
    const globalIndex = allImageUrls.indexOf(targetUrl);
    return globalIndex >= 0 ? globalIndex : currentImageIndex;
  }, [imageUrls, allImageUrls, currentImageIndex]);

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
      closeDeleteModal();
    } catch (error) {
      console.error('删除消息失败:', error);
    } finally {
      setIsDeleting(false);
    }
  };

  // 判断是否有媒体内容（需要更宽的显示区域）
  const hasMedia = hasImage || hasVideo || !!mediaPlaceholderInfo;

  return (
    <div
      data-message-id={message.id}
      className={`flex mb-4 ${isUser ? 'justify-end' : 'justify-start'}`}
    >
      <div
        className={`relative flex flex-col ${isUser ? 'items-end' : 'items-start'} ${hasMedia ? 'max-w-[90%]' : 'max-w-[80%]'}`}
        onMouseEnter={handleMouseEnter}
        onMouseLeave={handleMouseLeave}
      >
        {/* 用户消息：图片在上，文字在下（因为上传时已获取 CDN URL，图片先准备好） */}
        {isUser && (hasImage || hasVideo) && (
          <div className="mb-3">
            <MessageMedia
              imageUrls={imageUrls}
              videoUrls={videoUrls}
              messageId={message.id}
              isUser={isUser}
              onImageClick={(index) => {
                const globalIndex = index !== undefined ? getGlobalImageIndex(index) : currentImageIndex;
                setPreviewIndex(globalIndex);
                setShowImagePreview(true);
              }}
              onMediaLoaded={onMediaLoaded}
              isGenerating={!!mediaPlaceholderInfo}
              generatingType={mediaPlaceholderInfo?.type}
              imageAspectRatio={actualImageAspectRatio}
              videoAspectRatio={actualVideoAspectRatio}
            />
          </div>
        )}

        {/* 消息气泡（仅文字内容） */}
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
            {((isRegenerating || isStreaming) && !textContent) ? (
              <LoadingPlaceholder text={PLACEHOLDER_TEXT.CHAT_THINKING} />
            ) : mediaPlaceholderInfo ? (
              /* 媒体占位符：图片/视频生成中 */
              <LoadingPlaceholder text={mediaPlaceholderInfo.text} />
            ) : (
              <>
                {textContent}
                {/* 流式输出光标 */}
                {(isStreaming || isRegenerating) && textContent && (
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
        </div>

        {/* AI 消息：文字在上，图片在下 */}
        {!isUser && (
          <MessageMedia
            imageUrls={imageUrls}
            videoUrls={videoUrls}
            messageId={message.id}
            isUser={isUser}
            onImageClick={(index) => {
              const globalIndex = index !== undefined ? getGlobalImageIndex(index) : currentImageIndex;
              setPreviewIndex(globalIndex);
              setShowImagePreview(true);
            }}
            onMediaLoaded={onMediaLoaded}
            isGenerating={!!mediaPlaceholderInfo}
            generatingType={mediaPlaceholderInfo?.type}
            imageAspectRatio={actualImageAspectRatio}
            videoAspectRatio={actualVideoAspectRatio}
          />
        )}

        {/* 操作工具栏 */}
        <MessageActions
          messageId={message.id}
          content={textContent}
          isUser={isUser}
          isErrorMessage={isErrorMessage}
          isRegenerating={isRegenerating}
          isGenerating={!!mediaPlaceholderInfo}
          visible={showToolbar}
          onRegenerate={onRegenerate}
          onDeleteClick={onDelete ? openDeleteModal : undefined}
          onMouseEnter={handleToolbarMouseEnter}
          onMouseLeave={handleToolbarMouseLeave}
        />
      </div>

      {/* 删除确认弹框 */}
      <DeleteMessageModal
        isOpen={showDeleteModal}
        closing={deleteModalClosing}
        onClose={closeDeleteModal}
        onConfirm={handleDeleteConfirm}
        loading={isDeleting}
      />

      {/* 图片预览弹窗 */}
      {showImagePreview && previewImageUrls.length > 0 && (
        <ImagePreviewModal
          imageUrl={previewImageUrls[previewIndex]}
          onClose={() => setShowImagePreview(false)}
          filename={`image-${previewIndex + 1}`}
          onPrev={() => setPreviewIndex(Math.max(0, previewIndex - 1))}
          onNext={() => setPreviewIndex(Math.min(previewImageUrls.length - 1, previewIndex + 1))}
          hasPrev={previewIndex > 0}
          hasNext={previewIndex < previewImageUrls.length - 1}
          allImages={previewImageUrls}
          currentIndex={previewIndex}
          onSelectImage={setPreviewIndex}
        />
      )}
    </div>
  );
});
