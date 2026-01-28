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
import { useRegenerateHandlers } from '../../hooks/useRegenerateHandlers';
import { useConversationRuntimeStore } from '../../stores/useConversationRuntimeStore';
import { useChatStore } from '../../stores/useChatStore';
import type { UnifiedModel } from '../../constants/models';

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
    handleScroll,
    markNewMessages,
    resetScrollState,
  } = useScrollManager({ containerRef, messagesEndRef });

  // 记录上一次的对话 ID（用于检测对话切换）
  const prevConversationIdRef = useRef<string | null>(null);

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

  // 滚动位置存储（用于对话切换时保存/恢复滚动位置）
  const setScrollPosition = useChatStore((state) => state.setScrollPosition);
  const getScrollPosition = useChatStore((state) => state.getScrollPosition);
  const clearScrollPosition = useChatStore((state) => state.clearScrollPosition);

  // 获取运行时状态（乐观更新消息）
  // 注意：直接从 states Map 获取，避免 getState() 返回新对象导致无限循环
  const runtimeState = useConversationRuntimeStore(
    (state) => conversationId ? state.states.get(conversationId) : undefined
  );

  // 合并持久化消息和乐观更新消息（去重）
  const mergedMessages = useMemo(() => {
    if (!runtimeState || runtimeState.optimisticMessages.length === 0) {
      return messages;
    }

    // 创建持久化消息的ID集合
    const persistedIds = new Set(messages.map(m => m.id));

    // 创建持久化用户消息的内容集合（用于检测 temp- 消息是否已被替换）
    const persistedUserContents = new Set(
      messages.filter(m => m.role === 'user').map(m => m.content)
    );

    // 过滤出需要显示的乐观消息
    const newOptimisticMessages = runtimeState.optimisticMessages.filter(m => {
      // 已存在于持久化消息中（通过ID），跳过
      if (persistedIds.has(m.id)) return false;

      // temp- 用户消息：检查内容是否已有对应的持久化消息
      if (m.id.startsWith('temp-') && m.role === 'user') {
        // 如果持久化消息中已有相同内容的用户消息，说明已被替换
        return !persistedUserContents.has(m.content);
      }

      // streaming- AI消息需要区分聊天流式和媒体任务
      if (m.id.startsWith('streaming-')) {
        // 检查是否是当前聊天流式消息
        if (m.id === runtimeState.streamingMessageId) {
          // 聊天流式消息：显示（正在生成中）
          return true;
        }
        // 媒体任务占位符（streaming-${taskId}）：始终显示
        // 它们会在轮询完成后被 replaceMediaPlaceholder 替换为真实消息
        return true;
      }

      // 其他消息：显示
      return true;
    });

    // 合并并按时间排序
    const combined = [...messages, ...newOptimisticMessages];
    combined.sort((a, b) => new Date(a.created_at).getTime() - new Date(b.created_at).getTime());

    return combined;
  }, [messages, runtimeState]);

  // 重新生成相关状态
  const [regeneratingId, setRegeneratingId] = useState<string | null>(null);
  const [isRegeneratingAI, setIsRegeneratingAI] = useState(false);
  const regeneratingContentRef = useRef<string>('');

  // 滚动控制（防止重复滚动）
  const hasScrolledForConversationRef = useRef(false);
  const scrollToBottomRef = useRef(scrollToBottom);
  scrollToBottomRef.current = scrollToBottom;
  // 跟踪 loading 状态变化（确保滚动在 loading: true → false 时触发）
  const prevLoadingRef = useRef(true);
  // 消息引用（用于重新生成 effect，避免 messages 依赖导致频繁触发）
  const messagesRef = useRef(messages);
  messagesRef.current = messages;

  // 对话切换时保存旧对话滚动位置 + 重置状态
  useEffect(() => {
    const prevId = prevConversationIdRef.current;
    if (conversationId !== prevId) {
      // 保存旧对话的滚动位置（仅当用户滚走时才保存）
      if (prevId) {
        const container = containerRef.current;
        if (container && userScrolledAway) {
          setScrollPosition(prevId, container.scrollTop);
        } else if (prevId) {
          // 用户在底部，清除保存的位置（下次直接显示底部）
          clearScrollPosition(prevId);
        }
      }

      // 重置滚动状态，确保新对话从干净状态开始
      resetScrollState();
      hasScrolledForConversationRef.current = false;
      // 注意：不要重置 prevLoadingRef.current，让滚动 useEffect 自然等待 loading 状态变化
      // 否则会在对话切换时使用旧数据立即触发滚动
      prevMessageCountRef.current = 0; // 重置消息计数器，避免误触发滚动
      prevConversationIdRef.current = conversationId;
    }
  }, [conversationId, resetScrollState, userScrolledAway, setScrollPosition, clearScrollPosition]);

  // 加载消息
  useEffect(() => {
    const abortController = new AbortController();
    loadMessages(abortController.signal);

    return () => {
      abortController.abort();
    };
  }, [loadMessages]);

  // 消息加载完成后恢复滚动位置或定位到底部
  useEffect(() => {
    const wasLoading = prevLoadingRef.current;
    prevLoadingRef.current = loading;

    // 条件：loading 从 true 变为 false + 有消息 + 当前对话未定位过
    // 这确保消息已经是当前对话的消息（而非旧对话的残留数据）
    if (wasLoading && !loading && mergedMessages.length > 0 && !hasScrolledForConversationRef.current && conversationId) {
      hasScrolledForConversationRef.current = true;
      // 使用 requestAnimationFrame 确保 DOM 已更新
      requestAnimationFrame(() => {
        const container = containerRef.current;
        if (!container) return;

        const savedPosition = getScrollPosition(conversationId);
        if (savedPosition !== null) {
          // 恢复到之前的滚动位置（边界检查）
          const maxScroll = container.scrollHeight - container.clientHeight;
          container.scrollTop = Math.min(savedPosition, Math.max(0, maxScroll));
        } else {
          // 没有保存的位置，直接定位到底部（无动画）
          container.scrollTop = container.scrollHeight;
        }
      });
    }
  }, [loading, mergedMessages.length, conversationId, getScrollPosition]);

  // 新消息添加后自动滚动（发送消息、生成完成等场景）
  const prevMessageCountRef = useRef(0);
  useEffect(() => {
    const currentCount = mergedMessages.length;
    const prevCount = prevMessageCountRef.current;

    // 仅当消息数量增加时触发（避免删除消息时滚动）
    // 添加 hasScrolledForConversationRef 检查，确保初始定位完成后才触发，避免覆盖恢复的滚动位置
    if (currentCount > prevCount && prevCount > 0 && !userScrolledAway && hasScrolledForConversationRef.current) {
      // 使用 requestAnimationFrame 确保 DOM 已更新
      requestAnimationFrame(() => {
        scrollToBottomRef.current(true); // 平滑滚动
      });
    }

    prevMessageCountRef.current = currentCount;
  }, [mergedMessages.length, userScrolledAway]);

  // 流式内容更新时自动滚动（AI 输出时持续跟随）
  const prevStreamingContentLengthRef = useRef(0);
  useEffect(() => {
    // 获取当前流式消息的内容长度
    const streamingMessage = runtimeState?.streamingMessageId
      ? runtimeState.optimisticMessages.find(m => m.id === runtimeState.streamingMessageId)
      : null;
    const currentLength = streamingMessage?.content.length ?? 0;
    const prevLength = prevStreamingContentLengthRef.current;

    // 流式内容增长时触发滚动（用户未滚走 + 初始定位完成）
    if (currentLength > prevLength && prevLength > 0 && !userScrolledAway && hasScrolledForConversationRef.current) {
      // 使用 requestAnimationFrame 确保 DOM 已更新
      requestAnimationFrame(() => {
        scrollToBottomRef.current(false); // 瞬时定位，避免平滑滚动跟不上输出速度
      });
    }

    // 更新记录（无论是否滚动）
    prevStreamingContentLengthRef.current = currentLength;

    // 流式结束时重置
    if (!runtimeState?.streamingMessageId) {
      prevStreamingContentLengthRef.current = 0;
    }
  }, [runtimeState?.streamingMessageId, runtimeState?.optimisticMessages, userScrolledAway]);

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
      const isImageMessage = !!targetMessage.image_url;
      const isVideoMessage = !!targetMessage.video_url;

      if (isImageMessage) {
        await regenerateImageMessage(userMessage);
      } else if (isVideoMessage) {
        await regenerateVideoMessage(userMessage);
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
    if (isRegeneratingAI && regeneratingId) {
      // 使用 requestAnimationFrame 确保 DOM 已更新
      requestAnimationFrame(() => {
        if (!userScrolledAway) {
          // 使用 messagesRef 避免 messages 依赖导致频繁触发
          const isFailedMessageRegenerate = messagesRef.current.some(
            m => m.id === regeneratingId && m.content === ''
          );
          scrollToBottom(!isFailedMessageRegenerate);
        }
      });
    }
  }, [isRegeneratingAI, regeneratingId, scrollToBottom, userScrolledAway]);

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

            return (
              <MessageItem
                key={message.id || `message-${index}`}
                message={message}
                isStreaming={isStreaming}
                isRegenerating={isRegenerating}
                onRegenerate={handleRegenerate}
                onDelete={handleDelete}
                onMediaLoaded={handleMediaLoaded}
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
