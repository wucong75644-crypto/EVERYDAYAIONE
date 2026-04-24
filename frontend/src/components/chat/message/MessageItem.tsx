/**
 * 单条消息组件
 *
 * 支持用户消息和 AI 消息的不同样式
 * 组合 MessageMedia 和 MessageActions 子组件
 */

import { memo, useState, useRef, useEffect, useMemo, useCallback } from 'react';
import { m } from 'framer-motion';
import type { Message } from '../../../stores/useMessageStore';
import { getTextContent, getImageUrls, getVideoUrls, getFiles } from '../../../stores/useMessageStore';
import DeleteMessageModal from '../modals/DeleteMessageModal';
import ImagePreviewModal from '../media/ImagePreviewModal';
import MessageMedia from './MessageMedia';
import MessageActions from './MessageActions';
import { getSavedSettings } from '../../../utils/settingsStorage';
import { logger } from '../../../utils/logger';
import { useModalAnimation } from '../../../hooks/useModalAnimation';
import { useMessageAnimation } from '../../../hooks/useMessageAnimation';
import LoadingPlaceholder from './LoadingPlaceholder';
import MarkdownRenderer from './MarkdownRenderer';
import ThinkingBlock from './ThinkingBlock';
import ToolResultBlock from './ToolResultBlock';
import FileCardList from '../media/FileCard';
import SuggestionChips from './SuggestionChips';
import { RENDER_CONFIG, getCompletedBubbleText, type MessageType } from '../../../constants/placeholder';
import type { RenderInstruction } from '../../../types/render';
import type { AspectRatio, VideoAspectRatio } from '../../../constants/models';

/** 内联图表图片（带骨架屏占位 + 淡入）
 *  占位符使用 4:3 比例（matplotlib 默认），宽度 500px，高度 375px
 *  图片 onLoad 后淡入替换，骨架屏消失 */
function InlineChartImage({ url, alt, onClick }: {
  url: string; alt: string; onClick: () => void;
}) {
  const [loaded, setLoaded] = useState(false);
  return (
    <div className="my-3 relative" style={{ maxWidth: '500px' }}>
      {/* 骨架屏占位：4:3 比例，与 matplotlib 默认输出一致 */}
      {!loaded && (
        <div className="w-full rounded-xl bg-hover dark:bg-surface-dark-card animate-media-pulse flex items-center justify-center"
          style={{ aspectRatio: '4/3' }}
        >
          <svg className="w-10 h-10 text-text-disabled dark:text-text-tertiary" xmlns="http://www.w3.org/2000/svg"
            viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"
            strokeLinecap="round" strokeLinejoin="round"
          >
            <rect width="18" height="18" x="3" y="3" rx="2" ry="2" />
            <circle cx="9" cy="9" r="2" />
            <path d="m21 15-3.086-3.086a2 2 0 0 0-2.828 0L6 21" />
          </svg>
        </div>
      )}
      <img
        src={url}
        alt={alt}
        className={`rounded-xl shadow-sm max-w-full h-auto cursor-pointer transition-opacity duration-500 ${loaded ? 'opacity-100' : 'opacity-0 absolute inset-0'}`}
        onClick={onClick}
        loading="lazy"
        onLoad={() => setLoaded(true)}
      />
    </div>
  );
}

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
  /** 是否跳过进入动画（批量加载历史消息时） */
  skipEntryAnimation?: boolean;
  /** 单图重新生成回调（多图模式） */
  onRegenerateSingle?: (messageId: string, imageIndex: number) => void;
  /** Agent Loop 步骤提示（"正在搜索..." 等） */
  agentStepHint?: string;
  /** 流式思考内容 */
  streamingThinking?: string;
  /** 思考开始时间戳 */
  thinkingStartTime?: number;
  /** 是否启用 framer layout 动画（长对话时父级会禁用以保性能） */
  enableLayoutAnimation?: boolean;
  /** 建议问题列表（仅最后一条 AI 消息显示） */
  suggestions?: string[];
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
  const imageUrls = getImageUrls(message);
  const videoUrls = getVideoUrls(message);
  const files = getFiles(message);
  const hasImage = imageUrls.length > 0;
  const hasVideo = videoUrls.length > 0;
  const hasFiles = files.length > 0;

  // 检测是否为多内容块模式（含 tool_result / image / file block）
  const hasMultiBlocks = useMemo(() => {
    if (!Array.isArray(message.content)) return false;
    return message.content.some((p) =>
      p.type === 'tool_result' || p.type === 'image' || p.type === 'file'
    );
  }, [message.content]);

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
    const hasMediaContent = (genType === 'image' && hasImage) || (genType === 'video' && hasVideo);
    if (hasMediaContent) {
      const count = genType === 'image' ? (Number(genParams.num_images) || 1) : undefined;
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

    const hasContent = (genType === 'image' && hasImage) || (genType === 'video' && hasVideo);
    if (skipEntryAnimation || hasContent) return null;

    return { type: genType as 'image' | 'video', text: config.loadingText };
  }, [message.role, message.status, message.generation_params, hasImage, hasVideo, skipEntryAnimation]);

  // 失败的媒体任务信息（用于渲染"裂开"占位符）
  const failedMediaType = useMemo(() => {
    if (message.role !== 'assistant' || message.status !== 'failed') return null;

    const genType = message.generation_params?.type;
    if (!genType || genType === 'chat') return null;

    const hasContent = (genType === 'image' && hasImage) || (genType === 'video' && hasVideo);
    return hasContent ? null : (genType as 'image' | 'video');
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
    if (genType === 'image') {
      return !hasImage;
    }
    if (genType === 'video') {
      return !hasVideo;
    }

    // 4. 其他情况（包括聊天任务），pending 状态算生成中
    return true;
  }, [message.role, message.status, message.generation_params, hasImage, hasVideo, isStreaming]);

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

  // 图片点击回调（合并用户/AI 两处相同逻辑，稳定引用）
  const handleImageClick = useCallback((index?: number) => {
    const globalIndex = index !== undefined ? getGlobalImageIndex(index) : currentImageIndex;
    setPreviewIndex(globalIndex);
    setShowImagePreview(true);
  }, [getGlobalImageIndex, currentImageIndex]);

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
              imageUrls={imageUrls}
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
          className={`${
            isUser
              ? 'rounded-2xl px-5 py-3 bg-gradient-to-r from-[var(--color-user-bubble-from)] to-[var(--color-user-bubble-to)] text-text-on-accent'
              : 'text-text-primary'
          }`}
          style={isUser ? { boxShadow: 'inset 0 1px 0 rgba(255,255,255,0.22), 0 4px 16px rgba(0,0,0,0.06)' } : undefined}
        >
          {/* 思考过程折叠块（仅 AI 消息） */}
          {!isUser && (() => {
            const thinkingText = streamingThinking || genParams.thinking_content as string || '';
            const isThinkingNow = !!(isStreaming && streamingThinking && !textContent);
            if (!thinkingText && !isThinkingNow) return null;
            return (
              <ThinkingBlock
                content={thinkingText}
                isThinking={isThinkingNow}
                thinkingStartTime={thinkingStartTime}
              />
            );
          })()}

          {/* 消息文本 */}
          <div className={isUser ? 'text-[15px] leading-relaxed whitespace-pre-wrap' : ''}>
            {/* 加载状态：流式输出开始但内容为空
                - 有 streamingThinking 时 → ThinkingBlock 已在上方显示，不再重复
                - 有 agentStepHint 时 → 显示工具步骤提示（"正在查询订单..."）
                - 都没有时 → 仅显示脉冲圆点（Claude 风格，无卡片无文字） */}
            {((isRegenerating || isStreaming) && !textContent) ? (
              <LoadingPlaceholder text={agentStepHint || 'AI 正在思考'} />
            ) : (!isUser && !textContent && !hasImage && !hasVideo && !hasFiles && !isErrorMessage && !isStreaming && !isRegenerating) ? (
              /* 已完成但无内容（用户取消等场景） */
              <span className="text-text-disabled text-sm italic">已取消，点击「重新生成」重试</span>
            ) : bubbleTextInfo ? (
              /* 媒体任务气泡文字：图片/视频生成中或生成完成（仅 pending 状态） */
              bubbleTextInfo.hasAnimation ? (
                <LoadingPlaceholder text={bubbleTextInfo.text} />
              ) : (
                <span>{bubbleTextInfo.text}</span>
              )
            ) : isErrorMessage ? (
              <span className="text-[15px]">{textContent || 'Error occurred'}</span>
            ) : isUser ? (
              /* 用户消息：保持纯文本 */
              <>{textContent}</>
            ) : hasMultiBlocks ? (
              /* AI 消息（多块模式）：遍历 content 按 type 分发渲染
                 text / tool_result / image / file 混排，一份 content 顺序渲染
                 设计文档：TECH_内容块混排渲染架构.md §7.2 */
              <>
                {message.content.map((part, idx) => {
                  if (part.type === 'text' && (part as { text: string }).text) {
                    return (
                      <MarkdownRenderer
                        key={idx}
                        content={(part as { text: string }).text}
                      />
                    );
                  }
                  if (part.type === 'tool_result') {
                    const tr = part as { tool_name: string; text: string; files?: Array<{ url: string; name: string; mime_type: string; size?: number }> };
                    return (
                      <ToolResultBlock
                        key={idx}
                        toolName={tr.tool_name}
                        text={tr.text}
                        files={tr.files}
                      />
                    );
                  }
                  if (part.type === 'image' && (part as { url?: string }).url) {
                    const img = part as { url: string; alt?: string };
                    const imgIndex = imageUrls.indexOf(img.url);
                    return (
                      <InlineChartImage
                        key={idx}
                        url={img.url}
                        alt={img.alt || '生成的图表'}
                        onClick={() => handleImageClick(imgIndex >= 0 ? imgIndex : 0)}
                      />
                    );
                  }
                  if (part.type === 'file' && (part as { url?: string }).url) {
                    const fp = part as import('../../../types/message').FilePart;
                    return (
                      <div key={idx} className="my-2">
                        <FileCardList files={[fp]} />
                      </div>
                    );
                  }
                  return null;
                })}
              </>
            ) : (
              /* AI 消息（单块 / 流式）：Markdown 渲染 */
              <MarkdownRenderer
                content={textContent}
              />
            )}
          </div>

          {/* 流式状态指示器：整个流式期间始终显示在底部
              - 有 agentStepHint → 显示工具步骤（"正在执行代码"等）
              - 正在输出文字 → 显示"AI 正在输出"
              - 等待首 token → 由上方 LoadingPlaceholder 处理，这里不重复 */}
          {!isUser && (isStreaming || isRegenerating) && textContent && (
            <div className="mt-1.5">
              <LoadingPlaceholder text={agentStepHint || 'AI 正在输出'} />
            </div>
          )}
        </div>

        {/* AI 媒体生成消息（generate_image / generate_video）：保留 MessageMedia 全部功能
            聊天消息的 image/file 已在多块模式内联渲染，不走此通道
            设计文档：TECH_内容块混排渲染架构.md §7.1 */}
        {!isUser && isMediaMessage && (
          <MessageMedia
            imageUrls={imageUrls}
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
            imageUrls={[]}
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
    </m.div>
  );
});
