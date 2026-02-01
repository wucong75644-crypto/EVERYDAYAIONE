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

  // 使用消息加载 Hook（负责从后端加载并写入缓存）
  const { loading, loadMessages } = useMessageLoader({
    conversationId,
    onNewMessages: markNewMessages,
  });

  // 使用统一消息读取 Hook（自动合并持久化消息和临时消息）
  const mergedMessages = useUnifiedMessages(conversationId);

  // 获取当前对话标题（用于任务追踪）
  const currentConversationTitle = useChatStore((state) => state.currentConversationTitle);

  // 获取统一操作方法
  const { removeMessage, replaceMessage, appendMessage } = useChatStore();

  // 兼容层：为重新生成策略提供 setMessages 接口
  // 实际上调用 replaceMessage/appendMessage 更新缓存，UI 通过 useUnifiedMessages 自动响应
  const setMessages = useCallback(
    (updater: Message[] | ((prev: Message[]) => Message[])) => {
      if (!conversationId) return;

      // 如果是函数形式，计算新消息列表并找出变化
      if (typeof updater === 'function') {
        const newMessages = updater(mergedMessages);

        // ✅ 使用Map提升性能，O(1)查找，避免index错位
        const oldMessagesMap = new Map(mergedMessages.map(m => [m.id, m]));

        newMessages.forEach((newMsg) => {
          const oldMsg = oldMessagesMap.get(newMsg.id);  // ✅ ID匹配替代index匹配

          if (oldMsg && oldMsg !== newMsg) {
            // 消息被修改 → 替换缓存
            replaceMessage(conversationId, oldMsg.id, newMsg);
          } else if (!oldMsg && newMsg && !newMsg.id.startsWith('temp-') && !newMsg.id.startsWith('streaming-')) {
            // 新增持久化消息 → 追加缓存
            appendMessage(conversationId, newMsg);
          }
        });
      }
    },
    [conversationId, mergedMessages, replaceMessage, appendMessage]
  );

  // 获取运行时状态（仅用于滚动逻辑，不用于消息合并）
  const runtimeState = useConversationRuntimeStore(
    (state) => conversationId ? state.states.get(conversationId) : undefined
  );

  // 提取所有图片 URL（用于缩略图预览，支持逗号分隔的多图）
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

  // 计算每条消息的第一张图片索引（用于缩略图预览）
  const getImageIndex = useCallback((message: Message): number => {
    const firstUrl = getFirstImageUrl(message.image_url);
    if (!firstUrl) return -1;
    return imageUrlIndexMap.get(firstUrl) ?? -1;
  }, [imageUrlIndexMap]);

  // 重新生成相关状态
  const [regeneratingId, setRegeneratingId] = useState<string | null>(null);
  const [isRegeneratingAI, setIsRegeneratingAI] = useState(false);

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

      // 非临时消息需要调用后端 API 删除
      if (!isTemporaryMessage) {
        await deleteMessage(messageId);
      }

      // 使用统一方法删除缓存中的消息
      if (conversationId) {
        removeMessage(conversationId, messageId);
        // 同时清理 RuntimeStore 中的临时消息
        useConversationRuntimeStore.getState().removeOptimisticMessage(conversationId, messageId);
      }

      // 计算新的最后一条消息（用于更新对话列表）
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

  // 重置重新生成状态的辅助函数
  const resetRegeneratingState = useCallback(() => {
    setRegeneratingId(null);
    setIsRegeneratingAI(false);
  }, []);

  // 媒体加载完成回调（占位符渲染后触发滚动）
  const handleMediaLoaded = useCallback(() => {
    if (!userScrolledAway) {
      scrollToBottom(true);
    }
  }, [userScrolledAway, scrollToBottom]);

  // 使用重新生成处理器 hook（统一入口）
  const { handleRegenerate: doRegenerate } = useRegenerateHandlers({
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
  });

  // 处理重新生成（查找消息对，调用统一入口）
  const handleRegenerate = useCallback(async (messageId: string) => {
    if (!conversationId || regeneratingId) return;

    // 使用 mergedMessages 查找，因为新生成的消息可能在乐观更新中
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

    // 调用统一入口，自动判断类型和策略
    await doRegenerate(targetMessage, userMessage);
  }, [conversationId, mergedMessages, regeneratingId, doRegenerate]);


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
          {mergedMessages.map((message) => {
            const isRegenerating = message.id === regeneratingId;
            const isStreaming = message.id.startsWith('streaming-');
            const imageIndex = getImageIndex(message);

            return (
              <MessageItem
                key={message.id}
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
