/**
 * 消息区域组件（use-stick-to-bottom 版本）
 *
 * 显示对话消息列表，支持：
 * - 空状态显示
 * - 自动粘附底部滚动
 * - 加载更多历史消息
 * - 消息缓存（切换秒显）
 *
 * 重构记录：
 * - 2026-02-04：从 Virtua 迁移到 use-stick-to-bottom，彻底解决滚动问题
 */

import { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import { StickToBottom, useStickToBottomContext } from 'use-stick-to-bottom';
import { deleteMessage, type Message } from '../../services/message';
import MessageItem from './MessageItem';
import EmptyState from './EmptyState';
import LoadingSkeleton from './LoadingSkeleton';
import toast from 'react-hot-toast';
import { useMessageLoader } from '../../hooks/useMessageLoader';
import { useRegenerateHandlers } from '../../hooks/useRegenerateHandlers';
import { useUnifiedMessages } from '../../hooks/useUnifiedMessages';
import { useConversationRuntimeStore } from '../../stores/useConversationRuntimeStore';
import { useChatStore } from '../../stores/useChatStore';
import { parseImageUrls, getFirstImageUrl } from '../../utils/imageUtils';
import type { UnifiedModel } from '../../constants/models';

interface MessageAreaProps {
  conversationId: string | null;
  onDelete?: (messageId: string, newLastMessage?: string) => void;
  onMessageUpdate?: (newLastMessage: string) => void;
  modelId?: string | null;
  /** 当前用户选择的模型（用于重新生成） */
  selectedModel?: UnifiedModel | null;
}

/**
 * 懒加载触发器组件（使用 Intersection Observer）
 */
function LoadMoreTrigger({
  hasMore,
  loadingMore,
  onLoadMore
}: {
  hasMore: boolean;
  loadingMore: boolean;
  onLoadMore: () => void;
}) {
  const triggerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!hasMore || loadingMore) return;

    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting) {
          onLoadMore();
        }
      },
      { threshold: 0.1, rootMargin: '100px' }
    );

    const element = triggerRef.current;
    if (element) {
      observer.observe(element);
    }

    return () => {
      if (element) {
        observer.unobserve(element);
      }
    };
  }, [hasMore, loadingMore, onLoadMore]);

  if (!hasMore) return null;

  return (
    <div ref={triggerRef} className="h-1 w-full">
      {loadingMore && (
        <div className="flex justify-center py-4">
          <div className="animate-spin rounded-full h-6 w-6 border-b-2 border-blue-500" />
        </div>
      )}
    </div>
  );
}

/**
 * 滚动到底部按钮（使用 StickToBottom Context）
 */
function ScrollToBottomButton() {
  const { isAtBottom, scrollToBottom } = useStickToBottomContext();
  const [hasNewMessages, setHasNewMessages] = useState(false);

  // 监听 isAtBottom 变化，管理新消息提示
  useEffect(() => {
    if (isAtBottom) {
      setHasNewMessages(false);
    }
  }, [isAtBottom]);

  // 监听消息变化（通过 Context 外部传入）
  // 注意：这里简化处理，实际的新消息检测在 MessageArea 中

  if (isAtBottom) return null;

  return (
    <button
      onClick={() => {
        scrollToBottom();
        setHasNewMessages(false);
      }}
      className={`absolute bottom-6 left-1/2 transform -translate-x-1/2 px-4 py-2 rounded-full shadow-lg flex items-center justify-center transition-colors z-20 ${
        hasNewMessages
          ? 'bg-blue-600 text-white hover:bg-blue-700'
          : 'bg-white text-gray-600 hover:bg-gray-50 border border-gray-200'
      }`}
    >
      {hasNewMessages ? (
        <span className="text-sm font-medium">有新消息</span>
      ) : (
        <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 14l-7 7m0 0l-7-7m7 7V3" />
        </svg>
      )}
    </button>
  );
}

export default function MessageArea({
  conversationId,
  onDelete,
  onMessageUpdate,
  modelId = null,
  selectedModel = null,
}: MessageAreaProps) {
  // 使用消息加载 Hook（负责从后端加载并写入缓存）
  const { loading, hasMore, loadMessages, loadMore, loadingMore } = useMessageLoader({ conversationId });

  // 使用统一消息读取 Hook（自动合并持久化消息和临时消息）
  const mergedMessages = useUnifiedMessages(conversationId);

  // 使用 ref 存储 mergedMessages，避免 setMessages 依赖导致频繁重建
  const mergedMessagesRef = useRef(mergedMessages);
  mergedMessagesRef.current = mergedMessages;

  // 注意：use-stick-to-bottom 会自动处理滚动，无需手动判断流式状态

  // 获取当前对话标题（用于任务追踪）
  const currentConversationTitle = useChatStore((state) => state.currentConversationTitle);

  // 获取统一操作方法
  const { removeMessage, replaceMessage, appendMessage } = useChatStore();

  // 兼容层：为重新生成策略提供 setMessages 接口
  const setMessages = useCallback(
    (updater: Message[] | ((prev: Message[]) => Message[])) => {
      if (!conversationId) return;

      if (typeof updater === 'function') {
        const currentMessages = mergedMessagesRef.current;
        const newMessages = updater(currentMessages);
        const oldMessagesMap = new Map(currentMessages.map(m => [m.id, m]));

        newMessages.forEach((newMsg) => {
          const oldMsg = oldMessagesMap.get(newMsg.id);

          if (oldMsg && oldMsg !== newMsg) {
            replaceMessage(conversationId, oldMsg.id, newMsg);
          } else if (!oldMsg && newMsg && !newMsg.id.startsWith('temp-') && !newMsg.id.startsWith('streaming-')) {
            appendMessage(conversationId, newMsg);
          }
        });
      }
    },
    [conversationId, replaceMessage, appendMessage]
  );

  // 提取所有图片 URL（用于缩略图预览）
  const allImageUrls = useMemo(() => {
    return mergedMessages
      .filter(m => m.image_url)
      .flatMap(m => parseImageUrls(m.image_url));
  }, [mergedMessages]);

  // 创建图片 URL 索引 Map（O(1) 查找优化）
  const imageUrlIndexMap = useMemo(() => {
    const map = new Map<string, number>();
    allImageUrls.forEach((url, index) => map.set(url, index));
    return map;
  }, [allImageUrls]);

  // 计算每条消息的第一张图片索引
  const getImageIndex = useCallback((message: Message): number => {
    const firstUrl = getFirstImageUrl(message.image_url);
    if (!firstUrl) return -1;
    return imageUrlIndexMap.get(firstUrl) ?? -1;
  }, [imageUrlIndexMap]);

  // 重新生成相关状态
  const [regeneratingId, setRegeneratingId] = useState<string | null>(null);

  // 加载消息
  useEffect(() => {
    const abortController = new AbortController();
    loadMessages(abortController.signal);

    return () => {
      abortController.abort();
    };
  }, [loadMessages]);

  // 处理删除消息
  const handleDelete = useCallback(async (messageId: string) => {
    try {
      const isTemporaryMessage = messageId.startsWith('temp-') ||
                                 messageId.startsWith('error-') ||
                                 messageId.startsWith('streaming-');

      if (!isTemporaryMessage) {
        await deleteMessage(messageId);
      }

      if (conversationId) {
        removeMessage(conversationId, messageId);
        useConversationRuntimeStore.getState().removeOptimisticMessage(conversationId, messageId);
      }

      const updatedMergedMessages = mergedMessages.filter((msg) => msg.id !== messageId);
      const newLastMessage = updatedMergedMessages.length > 0
        ? updatedMergedMessages[updatedMergedMessages.length - 1].content
        : undefined;

      onDelete?.(messageId, newLastMessage);
      toast.success('消息已删除');
    } catch (error) {
      console.error('删除消息失败:', error);
      toast.error('删除失败，请重试');
    }
  }, [conversationId, mergedMessages, removeMessage, onDelete]);

  // 重置重新生成状态
  const resetRegeneratingState = useCallback(() => {
    setRegeneratingId(null);
  }, []);

  // 媒体加载完成回调（use-stick-to-bottom 会自动处理，这里保留接口兼容性）
  const handleMediaLoaded = useCallback(() => {
    // use-stick-to-bottom 的 resize="smooth" 会自动处理高度变化
  }, []);

  // 使用重新生成处理器 hook（添加临时状态管理）
  const setIsRegeneratingAI = useCallback(() => {
    // use-stick-to-bottom 自动管理滚动，无需手动状态
  }, []);

  const { handleRegenerate: doRegenerate } = useRegenerateHandlers({
    conversationId,
    conversationTitle: currentConversationTitle,
    setMessages,
    scrollToBottom: () => {}, // use-stick-to-bottom 会自动滚动
    onMessageUpdate,
    resetRegeneratingState,
    setRegeneratingId,
    setIsRegeneratingAI,
    modelId,
    selectedModel,
    userScrolledAway: false, // use-stick-to-bottom 自动管理
  });

  // 处理重新生成
  const handleRegenerate = useCallback(async (messageId: string) => {
    if (!conversationId || regeneratingId) return;

    const targetMessage = mergedMessages.find((m) => m.id === messageId);
    if (!targetMessage || targetMessage.role !== 'assistant') return;

    const aiIndex = mergedMessages.findIndex((m) => m.id === messageId);
    let userMessage: Message | null = null;
    for (let i = aiIndex - 1; i >= 0; i--) {
      if (mergedMessages[i].role === 'user') {
        userMessage = mergedMessages[i];
        break;
      }
    }

    if (!userMessage) {
      toast.error('未找到对应的用户消息');
      return;
    }

    await doRegenerate(targetMessage, userMessage);
  }, [conversationId, mergedMessages, regeneratingId, doRegenerate]);

  // 空状态
  if (!conversationId && mergedMessages.length === 0) {
    return <EmptyState hasConversation={false} />;
  }

  // 加载中骨架屏
  if (conversationId && mergedMessages.length === 0 && loading) {
    return <LoadingSkeleton />;
  }

  // 对话已选择但无消息
  if (conversationId && mergedMessages.length === 0 && !loading) {
    return (
      <div className="flex-1 flex items-center justify-center bg-white">
        <div className="text-center max-w-md px-4">
          <div className="w-16 h-16 bg-gray-100 rounded-full flex items-center justify-center mx-auto mb-4">
            <svg className="w-8 h-8 text-gray-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z" />
            </svg>
          </div>
          <h2 className="text-lg font-medium text-gray-900 mb-2">暂无消息</h2>
          <p className="text-gray-500 text-sm">在下方输入框中输入内容开始对话</p>
        </div>
      </div>
    );
  }

  return (
    <div className="flex-1 overflow-hidden relative">
      {/* use-stick-to-bottom 核心容器 */}
      <StickToBottom
        key={conversationId}  // 切换对话时重新挂载，避免加载历史消息时触发初始滚动
        className="h-full bg-white"
        resize="smooth"      // 图片/视频加载时平滑调整
        initial="instant"    // 初始加载瞬时滚动到底部
      >
        <StickToBottom.Content className="pt-6 pb-2">
          {/* 懒加载触发器（放在顶部） */}
          <LoadMoreTrigger
            hasMore={hasMore}
            loadingMore={loadingMore}
            onLoadMore={loadMore}
          />

          {/* 消息列表 */}
          <div className="max-w-4xl mx-auto px-4 space-y-4">
            {mergedMessages.map((message) => {
              const isRegenerating = message.id === regeneratingId;
              const isMessageStreaming = message.id.startsWith('streaming-');
              const imageIndex = getImageIndex(message);

              return (
                <MessageItem
                  key={message.id}
                  message={message}
                  isStreaming={isMessageStreaming}
                  isRegenerating={isRegenerating}
                  onRegenerate={handleRegenerate}
                  onDelete={handleDelete}
                  onMediaLoaded={handleMediaLoaded}
                  allImageUrls={allImageUrls}
                  currentImageIndex={imageIndex >= 0 ? imageIndex : 0}
                />
              );
            })}
          </div>
        </StickToBottom.Content>

        {/* 滚动到底部按钮（使用 Context） */}
        <ScrollToBottomButton />
      </StickToBottom>
    </div>
  );
}
