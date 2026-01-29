/**
 * 消息区域组件（重构版）
 *
 * 显示对话消息列表，支持：
 * - 空状态显示
 * - 消息列表
 * - 自动滚动
 * - 加载更多历史消息
 * - 消息缓存（切换秒显）
 */

import { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import { deleteMessage, type Message } from '../../services/message';
import MessageItem from './MessageItem';
import EmptyState from './EmptyState';
import LoadingSkeleton from './LoadingSkeleton';
import toast from 'react-hot-toast';
import { useMessageLoader } from '../../hooks/useMessageLoader';
import { useScrollManager } from '../../hooks/useScrollManager';
import { useMessageAreaScroll } from '../../hooks/useMessageAreaScroll';
import { useRegenerateHandlers } from '../../hooks/useRegenerateHandlers';
import { useConversationRuntimeStore } from '../../stores/useConversationRuntimeStore';
import { useChatStore } from '../../stores/useChatStore';
import type { UnifiedModel } from '../../constants/models';
import { mergeOptimisticMessages } from '../../utils/mergeOptimisticMessages';

interface MessageAreaProps {
  conversationId: string | null;
  onDelete?: (messageId: string, newLastMessage?: string) => void;
  onMessageUpdate?: (newLastMessage: string) => void;
  modelId?: string | null;
  /** 当前用户选择的模型（用于重新生成） */
  selectedModel?: UnifiedModel | null;
}

export default function MessageArea({
  conversationId,
  onDelete,
  onMessageUpdate,
  modelId = null,
  selectedModel = null,
}: MessageAreaProps) {
  // 使用滚动管理 Hook（统一管理所有滚动状态）
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const {
    showScrollButton,
    userScrolledAway,
    hasNewMessages,
    setUserScrolledAway,
    setHasNewMessages,
    scrollToBottom,
    scrollToBottomDebounced,
    scrollToElement,
    handleScroll,
    markNewMessages,
    resetScrollState,
  } = useScrollManager({ containerRef, messagesEndRef });

  // 使用消息加载 Hook（通过回调通知新消息）
  const {
    messages,
    setMessages,
    loading,
    loadMessages,
    toStoreMessage,
    getCachedMessages,
    updateCachedMessages,
  } = useMessageLoader({ conversationId, onNewMessages: markNewMessages });

  // 获取当前对话标题（用于任务追踪）
  const currentConversationTitle = useChatStore((state) => state.currentConversationTitle);

  // 获取运行时状态（乐观更新消息）
  // 注意：直接从 states Map 获取，避免 getState() 返回新对象导致无限循环
  const runtimeState = useConversationRuntimeStore(
    (state) => conversationId ? state.states.get(conversationId) : undefined
  );

  // 合并持久化消息和乐观更新消息（使用工具函数）
  const mergedMessages = useMemo(() => {
    return mergeOptimisticMessages(messages, runtimeState);
  }, [messages, runtimeState]);

  // 提取所有图片 URL（用于缩略图预览）
  const allImageUrls = useMemo(() => {
    return mergedMessages
      .filter(m => m.image_url)
      .map(m => m.image_url as string);
  }, [mergedMessages]);

  // 创建图片 URL 索引 Map（O(1) 查找优化）
  const imageUrlIndexMap = useMemo(() => {
    const map = new Map<string, number>();
    allImageUrls.forEach((url, index) => map.set(url, index));
    return map;
  }, [allImageUrls]);

  // 计算每条消息的图片索引（用于缩略图预览）
  const getImageIndex = useCallback((message: Message): number => {
    if (!message.image_url) return -1;
    return imageUrlIndexMap.get(message.image_url) ?? -1;
  }, [imageUrlIndexMap]);

  // 重新生成相关状态
  const [regeneratingId, setRegeneratingId] = useState<string | null>(null);
  const [isRegeneratingAI, setIsRegeneratingAI] = useState(false);
  const regeneratingContentRef = useRef<string>('');

  // 使用滚动行为管理 Hook（处理对话切换、消息加载、新消息等滚动逻辑）
  const { handleRegenerateScroll } = useMessageAreaScroll({
    conversationId,
    messages: mergedMessages,
    loading,
    containerRef,
    userScrolledAway,
    setUserScrolledAway,
    scrollToBottom,
    scrollToBottomDebounced,
    scrollToElement,
    resetScrollState,
    runtimeState: runtimeState ? {
      streamingMessageId: runtimeState.streamingMessageId,
      optimisticMessages: runtimeState.optimisticMessages,
    } : undefined,
  });

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

      const updatedMessages = messages.filter((msg) => msg.id !== messageId);
      setMessages(updatedMessages);

      // 使用 mergedMessages 过滤后获取最后一条消息（包含乐观更新消息）
      const updatedMergedMessages = mergedMessages.filter((msg) => msg.id !== messageId);
      const newLastMessage = updatedMergedMessages.length > 0
        ? updatedMergedMessages[updatedMergedMessages.length - 1].content
        : undefined;

      if (conversationId) {
        const cached = getCachedMessages(conversationId);
        if (cached) {
          updateCachedMessages(conversationId, updatedMessages.map(toStoreMessage), cached.hasMore);
        }
      }

      onDelete?.(messageId, newLastMessage);
      toast.success('消息已删除');
    } catch (error) {
      console.error('删除消息失败:', error);
      toast.error('删除失败，请重试');
    }
  }, [conversationId, messages, mergedMessages, setMessages, getCachedMessages, updateCachedMessages, toStoreMessage, onDelete]);

  // 重置重新生成状态的辅助函数
  const resetRegeneratingState = useCallback(() => {
    setRegeneratingId(null);
    setIsRegeneratingAI(false);
  }, []);

  // 媒体加载完成回调（占位符尺寸固定，无需滚动调整）
  const handleMediaLoaded = useCallback(() => {
    // 占位符和媒体尺寸固定，加载完成不改变布局，无需滚动
  }, []);

  // 使用重新生成处理器 hook
  const {
    regenerateFailedMessage,
    regenerateAsNewMessage,
    regenerateImageMessage,
    regenerateVideoMessage,
  } = useRegenerateHandlers({
    conversationId,
    conversationTitle: currentConversationTitle,
    setMessages,
    scrollToBottom,
    onMessageUpdate,
    resetRegeneratingState,
    setRegeneratingId,
    setIsRegeneratingAI,
    modelId,
    selectedModel,
    userScrolledAway,
    getCachedMessages,
    updateCachedMessages,
    toStoreMessage,
  });

  // 处理重新生成（主入口）
  // 注意：使用 mergedMessages 而不是 messages，因为新生成的消息可能在乐观更新中
  const handleRegenerate = useCallback(async (messageId: string) => {
    if (!conversationId || regeneratingId) return;

    // 使用 mergedMessages 查找，因为新生成的消息可能在乐观更新中而不在持久化消息中
    const targetMessage = mergedMessages.find((m) => m.id === messageId);
    if (!targetMessage || targetMessage.role !== 'assistant') return;

    // 查找对应的用户消息
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

    regeneratingContentRef.current = '';

    try {
      // 根据原始 AI 消息类型判断使用哪种重新生成策略
      // 优先检查 generation_params（失败消息没有 url，但有 params）
      const hasImageUrl = !!targetMessage.image_url;
      const hasVideoUrl = !!targetMessage.video_url;
      const hasImageParams = !!targetMessage.generation_params?.image;
      const hasVideoParams = !!targetMessage.generation_params?.video;

      const isImageMessage = hasImageUrl || hasImageParams;
      const isVideoMessage = hasVideoUrl || hasVideoParams;

      if (isImageMessage) {
        await regenerateImageMessage(userMessage, targetMessage.generation_params);
      } else if (isVideoMessage) {
        await regenerateVideoMessage(userMessage, targetMessage.generation_params);
      } else if (targetMessage.is_error === true) {
        await regenerateFailedMessage(messageId, targetMessage);
      } else {
        await regenerateAsNewMessage(userMessage);
      }
    } catch (error) {
      // 增强错误恢复：使用函数式 setState
      if (targetMessage.is_error === true) {
        setMessages((prev) =>
          prev.map((m) => (m.id === messageId ? targetMessage : m))
        );
      }

      resetRegeneratingState();

      const errorMsg = error instanceof Error ? error.message : '未知错误';
      toast.error(`重新生成失败: ${errorMsg}`);

      // 记录详细日志
      console.error('重新生成失败详情:', {
        messageId,
        conversationId,
        isError: targetMessage.is_error,
        error
      });
    }
  }, [conversationId, mergedMessages, regeneratingId, regenerateFailedMessage, regenerateAsNewMessage, regenerateImageMessage, regenerateVideoMessage, resetRegeneratingState, setMessages]);


  // 重新生成开始时自动滚动
  useEffect(() => {
    handleRegenerateScroll(regeneratingId, isRegeneratingAI);
  }, [isRegeneratingAI, regeneratingId, handleRegenerateScroll]);

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
    <div className="flex-1 flex flex-col relative min-h-0 h-full">
      {/* 可滚动的消息区域 */}
      <div ref={containerRef} onScroll={handleScroll} className="flex-1 overflow-y-auto bg-white">
        <div key={conversationId || 'no-conversation'} className="max-w-3xl mx-auto py-6 px-4 animate-fadeIn">
          {mergedMessages.map((message, index) => {
            const isRegenerating = message.id === regeneratingId;
            const isStreaming = message.id.startsWith('streaming-');
            const imageIndex = getImageIndex(message);

            return (
              <MessageItem
                key={message.id || `message-${index}`}
                message={message}
                isStreaming={isStreaming}
                isRegenerating={isRegenerating}
                onRegenerate={handleRegenerate}
                onDelete={handleDelete}
                onMediaLoaded={handleMediaLoaded}
                allImageUrls={allImageUrls}
                currentImageIndex={imageIndex >= 0 ? imageIndex : 0}
              />
            );
          })}
          <div ref={messagesEndRef} />
        </div>
      </div>

      {/* 回到底部按钮（相对于 MessageArea 底部定位） */}
      {showScrollButton && (
        <button
          onClick={() => {
            setUserScrolledAway(false);
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
      )}
    </div>
  );
}
