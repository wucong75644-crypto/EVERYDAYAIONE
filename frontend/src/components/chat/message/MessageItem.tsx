/**
 * 单条消息组件
 *
 * 支持用户消息和 AI 消息的不同样式
 * 组合 MessageMedia 和 MessageActions 子组件
 */

import { memo, useState, useRef, useEffect, useMemo, useCallback } from 'react';
import { createPortal } from 'react-dom';
import TextContextMenu from '../menus/TextContextMenu';
import { m } from 'framer-motion';
import { getTextContent, getImageAssets, getVideoUrls, getFiles } from '../../../stores/useMessageStore';
import DeleteMessageModal from '../modals/DeleteMessageModal';
import { usePreview } from '../../../preview/usePreview';
import PreviewHost from '../../../preview/PreviewHost';
import { fromImageAsset } from '../../../preview/toPreviewItem';
import MessageMedia from './MessageMedia';
import MessageActions from './MessageActions';
import { getSavedSettings } from '../../../utils/settingsStorage';
import { logger } from '../../../utils/logger';
import { useModalAnimation } from '../../../hooks/useModalAnimation';
import { useMessageAnimation } from '../../../hooks/useMessageAnimation';
import MessageBubbleContent from './MessageBubbleContent';
import SuggestionChips from './SuggestionChips';
import { RENDER_CONFIG, getCompletedBubbleText, type MessageType } from '../../../constants/placeholder';
import type { RenderInstruction } from '../../../types/render';
import type { AspectRatio, VideoAspectRatio } from '../../../constants/models';
import type { MessageItemProps } from './MessageItem.types';

export default memo(function MessageItem({
  message,
  isStreaming = false,
  isRegenerating = false,
  onRegenerate,
  onDelete,
  onMediaLoaded,
  allImageAssets = [],
  currentImageIndex = 0,
  skipEntryAnimation = false,
  onRegenerateSingle,
  agentStepHint,
  streamingThinking,
  thinkingStartTime,
  enableLayoutAnimation = true,
  suggestions,
}: MessageItemProps) {
  const isUser = message.role === 'user';

  // 消息动画管理
  const {
    entryAnimationClass,
    deleteAnimationClass,
  } = useMessageAnimation({ message, skipEntryAnimation });

  // 提取内容（兼容新旧格式）
  const textContent = getTextContent(message);
  const imageAssets = getImageAssets(message);
  const videoUrls = getVideoUrls(message);
  const files = getFiles(message);
  const hasImage = imageAssets.length > 0;
  const hasVideo = videoUrls.length > 0;
  const hasFiles = files.length > 0;

  // 检测是否为多内容块模式
  // tool_step / tool_result / image / file / form 均触发多块模式
  // thinking 单独在 ThinkingBlock 渲染，不触发
  const hasMultiBlocks = useMemo(() => {
    if (!Array.isArray(message.content)) return false;
    return message.content.some((p) =>
      p.type === 'tool_step' || p.type === 'tool_result' ||
      p.type === 'image' || p.type === 'file' || p.type === 'form' ||
      p.type === 'ecom_plan'
    );
  }, [message.content]);

  // 文件块提取：从 content 数组收集所有 file block，统一在文字内容后渲染（固定槽位）
  const fileBlocks = useMemo(() => {
    if (!Array.isArray(message.content)) return [];
    return message.content.filter(
      (p): p is import('../../../types/message').FilePart =>
        p.type === 'file' && !!((p as { url?: string }).url || (p as { workspace_path?: string }).workspace_path),
    );
  }, [message.content]);

  // 多块模式：所有内容在主内容区内联渲染（行业标准：Claude/ChatGPT 风格）
  // ThinkingBlock 只放模型推理，不放工具步骤

  // 判断是否为失败消息
  const isErrorMessage = message.status === 'failed' || message.is_error === true;

  // 获取当前高级设置（用于占位符动态尺寸）
  const savedSettings = useMemo(() => getSavedSettings(), []);

  // 计算实际使用的宽高比：已生成的媒体使用保存的参数，生成中使用当前设置
  const genParams = message.generation_params || {};
  const actualImageAspectRatio = (genParams.aspect_ratio ?? genParams.aspectRatio ?? savedSettings.image.aspectRatio) as AspectRatio;
  const actualVideoAspectRatio = (genParams.aspect_ratio ?? genParams.aspectRatio ?? savedSettings.video.aspectRatio) as VideoAspectRatio;

  // 媒体生成消息（generate_image / generate_video）走独立 MessageMedia 通道
  const isMediaMessage = !!genParams.type && genParams.type !== 'chat';

  // 气泡文字信息（优先级：_render > RENDER_CONFIG > 兜底）
  const bubbleTextInfo = useMemo(() => {
    if (message.role !== 'assistant') return null;

    const genType = message.generation_params?.type;
    if (!genType || genType === 'chat') return null;

    const config = RENDER_CONFIG[genType as Exclude<MessageType, 'chat'>];
    if (!config) return null;

    const render = message.generation_params?._render as RenderInstruction | undefined;

    // 有媒体内容：显示完成文字
    const hasMediaContent = ((genType === 'image' || genType === 'image_ecom') && hasImage) || (genType === 'video' && hasVideo);
    if (hasMediaContent) {
      const count = (genType === 'image' || genType === 'image_ecom') ? (Number(genParams.num_images) || 1) : undefined;
      const text = render?.bubble_text || getCompletedBubbleText(genType as MessageType, count);
      return { text, hasAnimation: false };
    }

    // pending 无内容：显示生成中文字
    if (message.status === 'pending') {
      const text = render?.placeholder_text || config.loadingText;
      return { text, hasAnimation: true };
    }

    return null;
  }, [message.role, message.status, message.generation_params, hasImage, hasVideo, genParams]);

  // 占位符信息（传给 MessageMedia，用于控制灰色占位符显示）
  const mediaPlaceholderInfo = useMemo(() => {
    if (message.role !== 'assistant' || message.status !== 'pending') return null;

    const genType = message.generation_params?.type;
    if (!genType || genType === 'chat') return null;

    const config = RENDER_CONFIG[genType as Exclude<MessageType, 'chat'>];
    if (!config) return null;

    const hasContent = ((genType === 'image' || genType === 'image_ecom') && hasImage) || (genType === 'video' && hasVideo);
    if (skipEntryAnimation || hasContent) return null;

    return { type: (genType === 'image_ecom' ? 'image' : genType) as 'image' | 'video', text: config.loadingText };
  }, [message.role, message.status, message.generation_params, hasImage, hasVideo, skipEntryAnimation]);

  // 失败的媒体任务信息（用于渲染"裂开"占位符）
  const failedMediaType = useMemo(() => {
    if (message.role !== 'assistant' || message.status !== 'failed') return null;

    const genType = message.generation_params?.type;
    if (!genType || genType === 'chat') return null;

    const hasContent = ((genType === 'image' || genType === 'image_ecom') && hasImage) || (genType === 'video' && hasVideo);
    return hasContent ? null : ((genType === 'image_ecom' ? 'image' : genType) as 'image' | 'video');
  }, [message.role, message.status, message.generation_params, hasImage, hasVideo]);

  // 是否真正在生成中（用于控制重新生成按钮，区别于占位符显示）
  const isActuallyGenerating = useMemo(() => {
    if (message.role !== 'assistant') return false;

    // 1. 如果正在流式输出，算作生成中（聊天消息）
    if (isStreaming) return true;

    // 2. 如果不是 pending 状态，不算生成中
    if (message.status !== 'pending') return false;

    // 3. 对于媒体生成任务，pending 状态且无 URL 时才算生成中
    const genType = message.generation_params?.type;
    if (genType === 'image' || genType === 'image_ecom') {
      return !hasImage;
    }
    if (genType === 'video') {
      return !hasVideo;
    }

    // 4. 其他情况（包括聊天任务），pending 状态算生成中
    return true;
  }, [message.role, message.status, message.generation_params, hasImage, hasVideo, isStreaming]);

  // 用户文字气泡右键菜单状态
  const userBubbleRef = useRef<HTMLDivElement>(null);
  const [textContextMenu, setTextContextMenu] = useState<{ x: number; y: number; selectedText: string } | null>(null);

  const handleUserBubbleContextMenu = useCallback((e: React.MouseEvent) => {
    if (!isUser) return;
    // 没有可引用的文字（纯媒体消息）就让浏览器默认菜单接管
    if (!textContent || !textContent.trim()) return;

    // 仅当选区落在当前气泡内部时才作为"选中引用"
    let selectedText = '';
    const sel = window.getSelection();
    if (sel && sel.rangeCount > 0 && !sel.isCollapsed) {
      const range = sel.getRangeAt(0);
      const bubble = userBubbleRef.current;
      if (bubble && bubble.contains(range.commonAncestorContainer)) {
        selectedText = sel.toString();
      }
    }

    e.preventDefault();
    setTextContextMenu({ x: e.clientX, y: e.clientY, selectedText });
  }, [isUser, textContent]);

  // 工具栏显示/隐藏状态
  const [showToolbar, setShowToolbar] = useState(false);
  const hideTimeoutRef = useRef<number | null>(null);
  const isMouseOnToolbarRef = useRef(false);

  // 删除弹框开关（动画由 Modal 内部统一处理）
  const {
    isOpen: showDeleteModal,
    open: openDeleteModal,
    close: closeDeleteModal,
  } = useModalAnimation();

  // 图片预览弹窗状态（统一收敛到 usePreview）
  const preview = usePreview();

  // 用于预览的图片列表：所有消息都使用全局资产列表（支持查看对话中所有图片）
  const previewImageAssets = allImageAssets;

  // 将消息内图片索引转换为全局索引
  const getGlobalImageIndex = useCallback((localIndex: number): number => {
    if (imageAssets.length === 0 || localIndex >= imageAssets.length) {
      return currentImageIndex;
    }
    const targetUrl = imageAssets[localIndex].originalUrl;
    const globalIndex = allImageAssets.findIndex((asset) => asset.originalUrl === targetUrl);
    return globalIndex >= 0 ? globalIndex : currentImageIndex;
  }, [imageAssets, allImageAssets, currentImageIndex]);

  // 图片点击回调（合并用户/AI 两处相同逻辑，稳定引用）
  const handleImageClick = useCallback((index?: number) => {
    const globalIndex = index !== undefined ? getGlobalImageIndex(index) : currentImageIndex;
    const items = previewImageAssets.map((asset, i) =>
      fromImageAsset(asset, `image-${i + 1}`),
    );
    preview.open(items, globalIndex);
  }, [getGlobalImageIndex, currentImageIndex, previewImageAssets, preview]);

  // 单图重新生成回调（绑定 message.id，稳定引用）
  const handleRegenerateSingle = useCallback((idx: number) => {
    onRegenerateSingle?.(message.id, idx);
  }, [onRegenerateSingle, message.id]);

  // 整体重新生成回调（绑定 message.id，稳定引用）
  const handleRegenerate = useCallback(() => {
    onRegenerate?.(message.id);
  }, [onRegenerate, message.id]);

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

  // 删除操作 loading 状态
  const [deleteLoading, setDeleteLoading] = useState(false);

  // 处理删除确认
  const handleDeleteConfirm = async () => {
    if (!onDelete) return;

    setDeleteLoading(true);
    try {
      await onDelete(message.id);
    } catch (error) {
      logger.error('messageItem', '删除消息失败', error);
    } finally {
      setDeleteLoading(false);
      closeDeleteModal();
    }
  };

  // 判断是否有媒体内容（需要更宽的显示区域）
  const hasMedia = hasImage || hasVideo || hasFiles || !!mediaPlaceholderInfo;

  return (
    <m.div
      data-message-id={message.id}
      className={`flex mb-4 ${isUser ? 'justify-end' : 'justify-start'} ${entryAnimationClass} ${deleteAnimationClass}`}
      // framer layout：消息删除/插入/重排时其他消息 spring 过渡到新位置
      // - 用 "position" 只动位置不测尺寸（流式更新友好）
      // - 长对话（>50 条）时父级传 enableLayoutAnimation=false 完全禁用，避免布局抖动
      // - 流式消息（isStreaming）也禁用，避免每次 delta 都触发布局测量
      layout={enableLayoutAnimation && !isStreaming ? 'position' : false}
    >
      <div
        className={`relative flex flex-col ${isUser ? 'items-end' : 'items-start w-full'} ${isUser ? (hasMedia ? 'max-w-[90%]' : 'max-w-[80%]') : ''}`}
        onMouseEnter={handleMouseEnter}
        onMouseLeave={handleMouseLeave}
      >
        {/* 用户消息：图片在上，文字在下（因为上传时已获取 CDN URL，图片先准备好） */}
        {isUser && (hasImage || hasVideo || hasFiles) && (
          <div className="mb-3 w-full">
            <MessageMedia
              imageAssets={imageAssets}
              videoUrls={videoUrls}
              files={files}
              messageId={message.id}
              isUser={isUser}
              onImageClick={handleImageClick}
              onMediaLoaded={onMediaLoaded}
              isGenerating={!!mediaPlaceholderInfo}
              generatingType={mediaPlaceholderInfo?.type}
              imageAspectRatio={actualImageAspectRatio}
              videoAspectRatio={actualVideoAspectRatio}
            />
          </div>
        )}

        {/* 消息气泡：用户消息有气泡框，AI 消息无框直接铺开（对齐千问/豆包风格）
            V3：用户气泡加内高光 (inset 0 1px 0 rgba(255,255,255,0.2))，
            制造"半透明玻璃"的光感效果 */}
        <div
          ref={isUser ? userBubbleRef : undefined}
          onContextMenu={isUser ? handleUserBubbleContextMenu : undefined}
          className={`${
            isUser
              ? 'rounded-2xl px-5 py-3 bg-[#6366f1] bg-gradient-to-r from-[var(--color-user-bubble-from)] to-[var(--color-user-bubble-to)] text-text-on-accent'
              : 'text-text-primary'
          }`}
          style={isUser ? { boxShadow: 'inset 0 1px 0 rgba(255,255,255,0.22), 0 4px 16px rgba(0,0,0,0.06)' } : undefined}
        >
          <MessageBubbleContent
            message={message}
            isUser={isUser}
            hasMultiBlocks={hasMultiBlocks}
            imageAssets={imageAssets}
            fileBlocks={fileBlocks}
            isStreaming={isStreaming}
            isRegenerating={isRegenerating}
            textContent={textContent}
            thinkingContent={genParams.thinking_content as string | undefined}
            hasImage={hasImage}
            hasVideo={hasVideo}
            hasFiles={hasFiles}
            isErrorMessage={isErrorMessage}
            suggestions={suggestions}
            bubbleTextInfo={bubbleTextInfo}
            agentStepHint={agentStepHint}
            streamingThinking={streamingThinking}
            thinkingStartTime={thinkingStartTime}
            onImageClick={handleImageClick}
            onRegenerateSingle={onRegenerateSingle ? handleRegenerateSingle : undefined}
          />
        </div>

        {/* AI 媒体生成消息（generate_image / generate_video）：保留 MessageMedia 全部功能
            聊天消息的 image/file 已在多块模式内联渲染，不走此通道
            设计文档：TECH_内容块混排渲染架构.md §7.1 */}
        {!isUser && isMediaMessage && (
          <MessageMedia
            imageAssets={imageAssets}
            videoUrls={videoUrls}
            files={files}
            messageId={message.id}
            isUser={isUser}
            onImageClick={handleImageClick}
            onMediaLoaded={onMediaLoaded}
            isGenerating={!!mediaPlaceholderInfo}
            generatingType={mediaPlaceholderInfo?.type}
            imageAspectRatio={actualImageAspectRatio}
            videoAspectRatio={actualVideoAspectRatio}
            numImages={Number(genParams.num_images) || 1}
            content={message.content}
            onRegenerateSingle={onRegenerateSingle ? handleRegenerateSingle : undefined}
            failedMediaType={failedMediaType}
            onRegenerate={onRegenerate ? handleRegenerate : undefined}
          />
        )}
        {/* AI 聊天消息：失败的媒体占位符（仅非 isMediaMessage 时需要） */}
        {!isUser && !isMediaMessage && failedMediaType && (
          <MessageMedia
            videoUrls={[]}
            messageId={message.id}
            isUser={isUser}
            onImageClick={handleImageClick}
            failedMediaType={failedMediaType}
            onRegenerate={onRegenerate ? handleRegenerate : undefined}
          />
        )}

        {/* 操作工具栏 */}
        <MessageActions
          messageId={message.id}
          content={textContent}
          isUser={isUser}
          isErrorMessage={isErrorMessage}
          isRegenerating={isRegenerating}
          isGenerating={isActuallyGenerating}
          visible={showToolbar}
          markdownContent={!isUser ? textContent : undefined}
          onRegenerate={onRegenerate}
          onDeleteClick={onDelete ? openDeleteModal : undefined}
          onMouseEnter={handleToolbarMouseEnter}
          onMouseLeave={handleToolbarMouseLeave}
        />

        {/* 建议问题按钮（仅最后一条已完成的 AI 消息） */}
        {!isUser && suggestions && suggestions.length > 0 && (
          <SuggestionChips suggestions={suggestions} />
        )}
      </div>

      {/* 删除确认弹框 */}
      <DeleteMessageModal
        isOpen={showDeleteModal}
        onClose={closeDeleteModal}
        onConfirm={handleDeleteConfirm}
        loading={deleteLoading}
      />

      {/* 图片预览弹窗（统一走 PreviewHost）*/}
      <PreviewHost
        state={preview.state}
        onClose={preview.close}
        onIndexChange={preview.setIndex}
      />

      {/* 用户文字气泡右键菜单（Portal 到 body，避免被 overflow-hidden 裁剪） */}
      {textContextMenu && isUser && createPortal(
        <TextContextMenu
          x={textContextMenu.x}
          y={textContextMenu.y}
          fullText={textContent}
          selectedText={textContextMenu.selectedText}
          messageId={message.id}
          onClose={() => setTextContextMenu(null)}
        />,
        document.body,
      )}
    </m.div>
  );
});
