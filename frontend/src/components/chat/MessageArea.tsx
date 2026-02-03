/**
 * 消息区域组件（虚拟滚动版）
 *
 * 显示对话消息列表，支持：
 * - 空状态显示
 * - 消息列表（虚拟滚动优化）
 * - 自动滚动
 * - 加载更多历史消息
 * - 消息缓存（切换秒显）
 *
 * 重构记录：
 * - 2026-02-02：使用 useVirtuaScroll 统一入口管理滚动
 * - 2026-02-03：从 Virtuoso 迁移到 Virtua，解决动态高度闪烁问题
 */

import { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import { VList } from 'virtua';
import { deleteMessage, type Message } from '../../services/message';
import MessageItem from './MessageItem';
import EmptyState from './EmptyState';
import LoadingSkeleton from './LoadingSkeleton';
import toast from 'react-hot-toast';
import { useMessageLoader } from '../../hooks/useMessageLoader';
import { useVirtuaScroll } from '../../hooks/useVirtuaScroll';
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
  // 使用消息加载 Hook（负责从后端加载并写入缓存）
  // 不传 onNewMessages，新消息标记由 store.markConversationUnread 和 useVirtuaScroll 处理
  const { loading, loadMessages } = useMessageLoader({ conversationId });

  // 使用统一消息读取 Hook（自动合并持久化消息和临时消息）
  const mergedMessages = useUnifiedMessages(conversationId);

  // 使用 ref 存储 mergedMessages，避免 setMessages 依赖导致频繁重建
  const mergedMessagesRef = useRef(mergedMessages);
  mergedMessagesRef.current = mergedMessages;

  // 获取运行时状态（用于判断是否正在流式生成）
  const runtimeState = useConversationRuntimeStore(
    (state) => conversationId ? state.states.get(conversationId) : undefined
  );

  // 判断是否正在流式生成
  const isStreaming = !!runtimeState?.streamingMessageId;

  // ✅ 使用统一的 Virtua 滚动管理 Hook
  const {
    vlistRef,
    userScrolledAway,
    hasNewMessages,
    showScrollButton,
    handleScroll,
    scrollToBottom,
    setUserScrolledAway,
    setHasNewMessages,
  } = useVirtuaScroll({
    conversationId,
    messages: mergedMessages,
    loading,
    isStreaming,
  });

  // 获取当前对话标题（用于任务追踪）
  const currentConversationTitle = useChatStore((state) => state.currentConversationTitle);

  // 获取统一操作方法
  const { removeMessage, replaceMessage, appendMessage } = useChatStore();

  // 兼容层：为重新生成策略提供 setMessages 接口
  // 使用 ref 读取 mergedMessages，避免依赖变化导致函数重建
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
  const [isRegeneratingAI, setIsRegeneratingAI] = useState(false);

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
    setIsRegeneratingAI(false);
  }, []);

  // 媒体加载完成回调
  const handleMediaLoaded = useCallback(() => {
    if (!userScrolledAway) {
      scrollToBottom(true);
    }
  }, [userScrolledAway, scrollToBottom]);

  // 使用重新生成处理器 hook
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

  // 重新生成开始时自动滚动
  useEffect(() => {
    if (isRegeneratingAI && regeneratingId && !userScrolledAway) {
      const isFailedMessageRegenerate = mergedMessages.some(
        m => m.id === regeneratingId && m.content === ''
      );
      scrollToBottom(!isFailedMessageRegenerate);
    }
  }, [isRegeneratingAI, regeneratingId, userScrolledAway, mergedMessages, scrollToBottom]);

  // 渲染单条消息的函数（用于 VList）
  // 注意：必须在所有 early return 之前定义，遵守 Hooks 规则
  const renderMessage = useCallback((message: Message) => {
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
  }, [regeneratingId, getImageIndex, handleRegenerate, handleDelete, handleMediaLoaded, allImageUrls]);

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
      {/* Virtua VList 虚拟滚动 */}
      <div className="flex-1 bg-white overflow-hidden" style={{ height: '100%' }}>
        <VList
          ref={vlistRef}
          key={conversationId || 'no-conversation'}
          data={mergedMessages}
          shift={true}
          bufferSize={8}
          onScroll={handleScroll}
          className="h-full"
          style={{ height: '100%', paddingTop: 24, paddingBottom: 8 }}
        >
          {(message) => (
            <div className="max-w-4xl mx-auto px-4">
              {renderMessage(message)}
            </div>
          )}
        </VList>
      </div>

      {/* 回到底部按钮 */}
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
