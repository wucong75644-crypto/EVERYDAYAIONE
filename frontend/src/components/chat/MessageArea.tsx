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

import { useState, useEffect, useRef, useCallback } from 'react';
import { deleteMessage, sendMessageStream, type Message } from '../../services/message';
import MessageItem from './MessageItem';
import EmptyState from './EmptyState';
import LoadingSkeleton from './LoadingSkeleton';
import toast from 'react-hot-toast';
import { useMessageLoader } from '../../hooks/useMessageLoader';
import { useScrollManager } from '../../hooks/useScrollManager';

interface MessageAreaProps {
  conversationId: string | null;
  refreshTrigger?: number;
  isWaitingForAI?: boolean;
  newMessage?: Message | null;
  streamingContent?: string;
  onDelete?: (messageId: string, newLastMessage?: string) => void;
  onMessageUpdate?: (newLastMessage: string) => void;
  modelId?: string | null;
}

export default function MessageArea({
  conversationId,
  refreshTrigger = 0,
  isWaitingForAI = false,
  newMessage = null,
  streamingContent = '',
  onDelete,
  onMessageUpdate,
  modelId = null,
}: MessageAreaProps) {
  // 使用消息加载 Hook
  const {
    messages,
    setMessages,
    loading,
    hasMore,
    hasNewMessages,
    setHasNewMessages,
    loadMessages,
    toStoreMessage,
    getCachedMessages,
    updateCachedMessages,
  } = useMessageLoader({ conversationId, refreshTrigger });

  // 使用滚动管理 Hook
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const {
    showScrollButton,
    userScrolledAway,
    setUserScrolledAway,
    scrollToBottom,
    handleScroll,
  } = useScrollManager({ containerRef, messagesEndRef });

  // 重新生成相关状态
  const [regeneratingId, setRegeneratingId] = useState<string | null>(null);
  const [isRegeneratingAI, setIsRegeneratingAI] = useState(false);
  const regeneratingContentRef = useRef<string>('');

  const prevStreamingContentRef = useRef('');

  // 加载消息
  useEffect(() => {
    const abortController = new AbortController();
    loadMessages(abortController.signal);

    return () => {
      abortController.abort();
      prevStreamingContentRef.current = '';
    };
  }, [loadMessages]);

  // 消息加载完成后滚动到底部
  useEffect(() => {
    if (!loading && messages.length > 0) {
      // 使用 requestAnimationFrame 确保 DOM 已更新
      requestAnimationFrame(() => {
        scrollToBottom(false); // 使用 instant 滚动，避免加载时的动画
      });
    }
  }, [loading, conversationId, messages.length, scrollToBottom]); // 仅在加载状态变化或对话切换时触发

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

      const newLastMessage = updatedMessages.length > 0
        ? updatedMessages[updatedMessages.length - 1].content
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
  }, [conversationId, messages, setMessages, getCachedMessages, updateCachedMessages, toStoreMessage, onDelete]);

  // 处理重新生成
  // 策略：失败消息原地重试，成功消息新增对话
  const handleRegenerate = useCallback(async (messageId: string) => {
    if (!conversationId || regeneratingId) return;

    const targetMessage = messages.find((m) => m.id === messageId);
    if (!targetMessage || targetMessage.role !== 'assistant') return;

    // 查找对应的用户消息
    const aiMessageIndex = messages.findIndex((m) => m.id === messageId);
    let userMessage: Message | null = null;
    for (let i = aiMessageIndex - 1; i >= 0; i--) {
      if (messages[i].role === 'user') {
        userMessage = messages[i];
        break;
      }
    }

    if (!userMessage) {
      alert('未找到对应的用户消息');
      return;
    }

    // 判断是否为失败消息
    const isFailedMessage = targetMessage.is_error === true;

    try {
      regeneratingContentRef.current = '';

      if (isFailedMessage) {
        // 策略 A：失败消息 - 原地重新生成
        setRegeneratingId(messageId);
        setIsRegeneratingAI(true);

        const tempRegeneratingMessage: Message = {
          id: messageId,
          conversation_id: conversationId,
          role: 'assistant',
          content: '',
          image_url: null,
          video_url: null,
          credits_cost: 0,
          created_at: new Date().toISOString(),
        };

        const aiIndex = messages.findIndex((m) => m.id === messageId);
        const newMessages = [
          ...messages.slice(0, aiIndex),
          tempRegeneratingMessage,
          ...messages.slice(aiIndex + 1),
        ];
        setMessages(newMessages);

        await sendMessageStream(
          conversationId,
          {
            content: userMessage.content,
            model_id: modelId || 'gemini-3-flash',
          },
          {
            onContent: (content: string) => {
              regeneratingContentRef.current += content;

              const updatedMessage: Message = {
                ...tempRegeneratingMessage,
                content: regeneratingContentRef.current,
              };

              const updatedMessages = [
                ...messages.slice(0, aiIndex),
                updatedMessage,
                ...messages.slice(aiIndex + 1),
              ];
              setMessages(updatedMessages);

              if (!userScrolledAway) {
                scrollToBottom();
              }
            },
            onDone: (finalMessage: Message | null) => {
              if (!finalMessage) return;
              const finalMessages = messages.map((m) =>
                m.id === messageId ? finalMessage : m
              );
              setMessages(finalMessages);

              if (conversationId) {
                const cached = getCachedMessages(conversationId);
                if (cached) {
                  updateCachedMessages(conversationId, finalMessages.map(toStoreMessage), cached.hasMore);
                }
              }

              setRegeneratingId(null);
              setIsRegeneratingAI(false);
              regeneratingContentRef.current = '';

              if (onMessageUpdate) {
                onMessageUpdate(finalMessage.content);
              }
            },
            onError: (error: string) => {
              console.error('重试失败:', error);

              const originalMessages = messages.map((m) =>
                m.id === messageId ? targetMessage : m
              );
              setMessages(originalMessages);

              setRegeneratingId(null);
              setIsRegeneratingAI(false);
              regeneratingContentRef.current = '';

              toast.error(`重试失败: ${error}`);
            },
          }
        );
      } else {
        // 策略 B：成功消息 - 新增用户消息副本 + 新增 AI 占位消息
        const newStreamingId = `streaming-${Date.now()}`;
        setRegeneratingId(newStreamingId);
        setIsRegeneratingAI(true);

        // 创建用户消息副本（临时 ID，等待后端返回真实消息）
        const tempUserMessage: Message = {
          id: `temp-user-${Date.now()}`,
          conversation_id: conversationId,
          role: 'user',
          content: userMessage.content,
          image_url: userMessage.image_url,
          video_url: null,
          credits_cost: 0,
          created_at: new Date().toISOString(),
        };

        // 创建 AI 占位消息
        const tempAiMessage: Message = {
          id: newStreamingId,
          conversation_id: conversationId,
          role: 'assistant',
          content: '',
          image_url: null,
          video_url: null,
          credits_cost: 0,
          created_at: new Date().toISOString(),
        };

        // 追加到消息列表末尾
        const newMessages = [...messages, tempUserMessage, tempAiMessage];
        setMessages(newMessages);
        scrollToBottom();

        await sendMessageStream(
          conversationId,
          {
            content: userMessage.content,
            model_id: modelId || 'gemini-3-flash',
          },
          {
            onUserMessage: (realUserMessage: Message) => {
              // 替换临时用户消息为真实消息
              setMessages((prev) =>
                prev.map((m) => (m.id === tempUserMessage.id ? realUserMessage : m))
              );
            },
            onContent: (content: string) => {
              regeneratingContentRef.current += content;

              setMessages((prev) =>
                prev.map((m) =>
                  m.id === newStreamingId
                    ? { ...m, content: regeneratingContentRef.current }
                    : m
                )
              );

              if (!userScrolledAway) {
                scrollToBottom();
              }
            },
            onDone: (finalMessage: Message | null) => {
              if (finalMessage) {
                setMessages((prev) =>
                  prev.map((m) => (m.id === newStreamingId ? finalMessage : m))
                );

                if (conversationId) {
                  const cached = getCachedMessages(conversationId);
                  if (cached) {
                    // 获取最新的消息列表用于缓存更新
                    setMessages((prev) => {
                      const updated = prev.map((m) => (m.id === newStreamingId ? finalMessage : m));
                      updateCachedMessages(conversationId, updated.map(toStoreMessage), cached.hasMore);
                      return updated;
                    });
                  }
                }

                if (onMessageUpdate) {
                  onMessageUpdate(finalMessage.content);
                }
              }

              setRegeneratingId(null);
              setIsRegeneratingAI(false);
              regeneratingContentRef.current = '';
            },
            onError: (error: string) => {
              console.error('重新生成失败:', error);

              // 移除临时添加的消息
              setMessages((prev) =>
                prev.filter((m) => m.id !== tempUserMessage.id && m.id !== newStreamingId)
              );

              setRegeneratingId(null);
              setIsRegeneratingAI(false);
              regeneratingContentRef.current = '';

              toast.error(`重新生成失败: ${error}`);
            },
          }
        );
      }
    } catch (error) {
      console.error('重新生成失败:', error);

      if (isFailedMessage) {
        const originalMessages = messages.map((m) =>
          m.id === messageId ? targetMessage : m
        );
        setMessages(originalMessages);
      }

      setRegeneratingId(null);
      setIsRegeneratingAI(false);
      regeneratingContentRef.current = '';
      toast.error('重新生成失败，请重试');
    }
  }, [conversationId, messages, setMessages, regeneratingId, modelId, onMessageUpdate, userScrolledAway, scrollToBottom, getCachedMessages, updateCachedMessages, toStoreMessage]);

  // 新消息或流式内容变化时更新显示和滚动
  useEffect(() => {
    if (newMessage && newMessage.id && messages.every((m) => m.id !== newMessage.id)) {
      setMessages((prev) => [...prev, newMessage]);

      if (conversationId) {
        const storeMessage = toStoreMessage(newMessage);
        const cached = getCachedMessages(conversationId);
        if (cached) {
          updateCachedMessages(conversationId, [...cached.messages, storeMessage], hasMore);
        }
      }

      if (!userScrolledAway) {
        setTimeout(() => scrollToBottom(), 100);
      }
    }
  }, [newMessage, conversationId, messages, setMessages, userScrolledAway, scrollToBottom, hasMore, toStoreMessage, getCachedMessages, updateCachedMessages]);

  useEffect(() => {
    if (streamingContent && streamingContent !== prevStreamingContentRef.current) {
      prevStreamingContentRef.current = streamingContent;
      if (!userScrolledAway) {
        scrollToBottom();
      }
    }
  }, [streamingContent, userScrolledAway, scrollToBottom]);

  // 重新生成开始时自动滚动
  useEffect(() => {
    if (isRegeneratingAI && regeneratingId) {
      setTimeout(() => {
        if (!userScrolledAway) {
          const isFailedMessageRegenerate = messages.some(
            m => m.id === regeneratingId && m.content === ''
          );
          scrollToBottom(!isFailedMessageRegenerate);
        }
      }, 150);
    }
  }, [isRegeneratingAI, regeneratingId, messages, scrollToBottom, userScrolledAway]);

  // 空状态
  if (!conversationId && messages.length === 0) {
    return <EmptyState hasConversation={false} />;
  }

  // 加载中骨架屏
  if (conversationId && messages.length === 0 && loading) {
    return <LoadingSkeleton />;
  }

  // 对话已选择但无消息
  if (conversationId && messages.length === 0 && !loading) {
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
          {messages.map((message, index) => {
            const isStreaming = message.id?.startsWith('streaming-') && isWaitingForAI;
            const isRegenerating = message.id === regeneratingId;

            return (
              <MessageItem
                key={message.id || `message-${index}`}
                message={message}
                isStreaming={isStreaming}
                isRegenerating={isRegenerating}
                onRegenerate={handleRegenerate}
                onDelete={handleDelete}
              />
            );
          })}

          {/* AI 思考中加载状态（仅新消息等待时显示，重新生成时由 MessageItem 内部处理） */}
          {isWaitingForAI && !isRegeneratingAI && !streamingContent && (
            <div className="flex mb-4 justify-start">
              <div className="max-w-[70%] rounded-2xl px-4 py-3 bg-white border border-gray-200">
                <div className="flex items-center space-x-2 text-gray-500">
                  <div className="flex space-x-1">
                    <span className="w-2 h-2 bg-gray-400 rounded-full animate-bounce" style={{ animationDelay: '0ms' }}></span>
                    <span className="w-2 h-2 bg-gray-400 rounded-full animate-bounce" style={{ animationDelay: '150ms' }}></span>
                    <span className="w-2 h-2 bg-gray-400 rounded-full animate-bounce" style={{ animationDelay: '300ms' }}></span>
                  </div>
                  <span className="text-sm">AI 正在思考...</span>
                </div>
              </div>
            </div>
          )}
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
