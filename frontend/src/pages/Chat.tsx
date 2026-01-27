/**
 * 聊天页面
 *
 * 主要功能：
 * - 与 AI 对话生成图片/视频
 * - 查看历史对话
 * - 管理对话（重命名、删除）
 */

import { useState, useEffect, useCallback, useRef } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { useAuthStore } from '../stores/useAuthStore';
import { useTaskStore } from '../stores/useTaskStore';
import { useConversationRuntimeStore } from '../stores/useConversationRuntimeStore';
import { useChatStore } from '../stores/useChatStore';
import Sidebar from '../components/chat/Sidebar';
import MessageArea from '../components/chat/MessageArea';
import InputArea from '../components/chat/InputArea';
import { updateConversation, getConversation } from '../services/conversation';
import type { Message } from '../services/message';
import type { UnifiedModel } from '../constants/models';
import toast from 'react-hot-toast';

/**
 * 将 Message 添加到本地缓存（避免重复的类型转换代码）
 */
function addMessageToLocalCache(conversationId: string, message: Message): void {
  useChatStore.getState().addMessageToCache(conversationId, {
    id: message.id,
    role: message.role as 'user' | 'assistant',
    content: message.content,
    imageUrl: message.image_url ?? undefined,
    videoUrl: message.video_url ?? undefined,
    createdAt: message.created_at,
  });
}

export default function Chat() {
  const navigate = useNavigate();
  const { id: urlConversationId } = useParams<{ id?: string }>();
  const { isAuthenticated, user, refreshUser } = useAuthStore();
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [currentConversationId, setCurrentConversationId] = useState<string | null>(null);
  const [conversationTitle, setConversationTitle] = useState('新对话');
  const [conversationModelId, setConversationModelId] = useState<string | null>(null);
  // 当前用户选择的模型（由 InputArea 同步，用于 MessageArea 重新生成）
  const [currentSelectedModel, setCurrentSelectedModel] = useState<UnifiedModel | null>(null);

  // 使用 ref 保存最新的 currentConversationId（避免 useCallback 闭包陷阱）
  const currentConversationIdRef = useRef<string | null>(null);
  currentConversationIdRef.current = currentConversationId;

  // 对话列表乐观更新（发送消息时立即将对话移到最前）
  const [conversationOptimisticUpdate, setConversationOptimisticUpdate] = useState<{
    conversationId: string;
    lastMessage: string;
  } | null>(null);

  // 标题乐观更新（顶部标题修改时同步更新侧边栏）
  const [conversationOptimisticTitleUpdate, setConversationOptimisticTitleUpdate] = useState<{
    id: string;
    title: string;
  } | null>(null);

  // 新对话乐观更新（创建新对话时立即添加到列表顶部）
  const [conversationOptimisticNew, setConversationOptimisticNew] = useState<{
    id: string;
    title: string;
  } | null>(null);

  // 顶部标题编辑状态
  const [isEditingTitle, setIsEditingTitle] = useState(false);
  const [editingTitle, setEditingTitle] = useState('');

  // 未登录用户重定向到登录页
  useEffect(() => {
    if (!isAuthenticated) {
      navigate('/login');
    }
  }, [isAuthenticated, navigate]);

  // 页面加载时刷新用户信息（包括积分）
  useEffect(() => {
    if (isAuthenticated) {
      refreshUser();
    }
  }, [isAuthenticated, refreshUser]);

  // 监听 URL 参数变化，同步到状态
  useEffect(() => {
    // LRU清理：保留当前对话 + 活跃任务对话 + 正在生成的对话
    if (urlConversationId) {
      const activeTaskIds = useTaskStore.getState().getActiveConversationIds();

      // 查找所有正在生成（streaming）的对话，避免删除它们的状态
      const activeStreamingIds: string[] = [];
      runtimeStore.states.forEach((state, conversationId) => {
        if (state.isGenerating || state.streamingMessageId) {
          activeStreamingIds.push(conversationId);
        }
      });

      // 合并所有需要保留的对话ID（去重后保留前10个）
      const keepIds = Array.from(new Set([
        urlConversationId,
        ...activeTaskIds,
        ...activeStreamingIds,
      ])).slice(0, 10);

      runtimeStore.cleanup(keepIds);
    }

    if (urlConversationId) {
      // 立即设置对话 ID（无需等待 API，让 MessageArea 立即加载缓存）
      setCurrentConversationId(urlConversationId);
      setConversationTitle('加载中...');

      // 异步加载对话详情（只更新 title 和 modelId）
      getConversation(urlConversationId)
        .then((conversation) => {
          // 只有当前 URL 还是这个对话时才更新（避免快速切换导致的状态错乱）
          if (urlConversationId === conversation.id) {
            setConversationTitle(conversation.title);
            setConversationModelId(conversation.model_id);
          }
        })
        .catch((error) => {
          console.error('加载 URL 对话失败:', error);
          // 对话不存在或加载失败，跳转回主聊天页
          navigate('/chat');
        });
    } else {
      // URL 中没有对话 ID，清除状态（回到主页）
      setCurrentConversationId(null);
      setConversationTitle('新对话');
      setConversationModelId(null);
    }
  }, [urlConversationId, navigate]);

  // 创建新对话
  const handleNewConversation = () => {
    // 跳转到主聊天页面（会触发 URL 变化，自动清除状态）
    navigate('/chat');
  };

  // 选择对话
  const handleSelectConversation = (id: string, _title: string, _modelId?: string | null) => {
    // 路由跳转到对话详情页（URL 变化会触发 useEffect 统一处理状态更新）
    navigate(`/chat/${id}`);
  };

  // 对话创建成功
  const handleConversationCreated = (id: string, title: string) => {
    setCurrentConversationId(id);
    setConversationTitle(title);
    // 乐观更新：直接添加新对话到列表顶部（不触发 API 调用）
    setConversationOptimisticNew({ id, title });
  };

  // 任务状态管理
  const { startTask, updateTaskContent, completeTask, failTask, markNotificationRead, clearRecentlyCompleted } = useTaskStore();

  // RuntimeStore（新架构）
  const runtimeStore = useConversationRuntimeStore();

  // 消息开始发送（乐观更新）
  const handleMessagePending = useCallback((message: Message) => {
    const messageConversationId = message.conversation_id;

    // 启动任务追踪（所有对话都追踪，仅用户消息）
    if (messageConversationId && message.role === 'user') {
      startTask(messageConversationId, conversationTitle);
    }

    // 添加/替换RuntimeStore消息
    if (messageConversationId) {
      if (message.role === 'user') {
        if (message.id.startsWith('temp-')) {
          // 临时用户消息：添加到乐观更新
          runtimeStore.addOptimisticUserMessage(messageConversationId, message);
        } else {
          // 真实用户消息（后端返回）：替换匹配的temp-消息
          runtimeStore.replaceOptimisticMessage(messageConversationId, message);
          // 同时添加到缓存，确保切换对话后消息仍然显示
          addMessageToLocalCache(messageConversationId, message);
        }
      } else if (message.role === 'assistant' && message.id.startsWith('streaming-')) {
        // 媒体任务占位符（图片/视频生成中）
        runtimeStore.addMediaPlaceholder(messageConversationId, message);
      }
    }

    // 侧边栏乐观更新（只有当前对话）
    if (messageConversationId === currentConversationIdRef.current) {
      setConversationOptimisticUpdate({
        conversationId: currentConversationIdRef.current,
        lastMessage: message.content,
      });
    }
  }, [conversationTitle, startTask, runtimeStore]);

  // 消息发送成功（接收 AI 回复）
  const handleMessageSent = useCallback((aiMessage?: Message | null) => {
    const messageConversationId = aiMessage?.conversation_id;

    // 完成任务追踪
    if (messageConversationId) {
      if (aiMessage?.is_error) {
        failTask(messageConversationId);
      } else {
        completeTask(messageConversationId);
      }
    }

    // 完成流式生成
    if (messageConversationId) {
      // 如果是错误消息，先添加错误消息再完成streaming
      if (aiMessage && aiMessage.is_error) {
        runtimeStore.addErrorMessage(messageConversationId, aiMessage);
      } else if (aiMessage && (aiMessage.image_url || aiMessage.video_url)) {
        // 图片/视频生成完成：已在 handleMediaPolling.onSuccess 中通过
        // replaceMediaPlaceholder + addMessageToCache 完成处理
        // 这里不再重复操作，避免消息重复（duplicate key 错误）

        // 更新侧边栏显示（如果是当前对话）
        if (messageConversationId === currentConversationIdRef.current) {
          setConversationOptimisticUpdate({
            conversationId: messageConversationId,
            lastMessage: aiMessage.content,
          });
        }
      } else {
        // 普通聊天流式生成完成
        runtimeStore.completeStreaming(messageConversationId);
      }

      // 用户正在查看当前对话，清除闪烁状态
      if (messageConversationId === currentConversationIdRef.current) {
        clearRecentlyCompleted(messageConversationId);
      }
    }

    // 如果是其他对话的消息完成，显示通知
    if (messageConversationId && messageConversationId !== currentConversationIdRef.current && aiMessage && !aiMessage.is_error) {
      toast.success(
        (t) => (
          <span
            className="cursor-pointer"
            onClick={() => {
              toast.dismiss(t.id);
              markNotificationRead(messageConversationId);
              navigate(`/chat/${messageConversationId}`);
            }}
          >
            对话任务已完成，点击查看
          </span>
        ),
        { duration: 5000 }
      );
    }

    refreshUser();
  }, [refreshUser, completeTask, failTask, markNotificationRead, clearRecentlyCompleted, navigate, runtimeStore]);

  // AI开始生成（创建streaming消息）
  const handleStreamStart = useCallback((conversationId: string, _model: string) => {
    // 创建streaming消息
    const now = Date.now();
    const streamingId = now.toString();
    runtimeStore.startStreaming(conversationId, streamingId, new Date(now).toISOString());
  }, [runtimeStore]);

  // 流式内容更新
  const handleStreamContent = useCallback((text: string, conversationId: string) => {
    // 更新任务流式内容（所有对话都追踪）
    updateTaskContent(conversationId, text);

    // 追加流式内容到RuntimeStore（所有对话都追踪）
    runtimeStore.appendStreamingContent(conversationId, text);
  }, [updateTaskContent, runtimeStore]);

  // 切换侧边栏
  const toggleSidebar = () => {
    setSidebarCollapsed(!sidebarCollapsed);
  };

  // 顶部标题双击编辑
  const handleTitleDoubleClick = () => {
    if (currentConversationId) {
      setEditingTitle(conversationTitle);
      setIsEditingTitle(true);
    }
  };

  // 提交标题编辑
  const handleTitleSubmit = async () => {
    const newTitle = editingTitle.trim();
    if (!newTitle || newTitle === conversationTitle || !currentConversationId) {
      setIsEditingTitle(false);
      return;
    }
    // 乐观更新顶部标题
    setConversationTitle(newTitle);
    setIsEditingTitle(false);
    // 乐观更新侧边栏（立即同步，不等待 API）
    setConversationOptimisticTitleUpdate({ id: currentConversationId, title: newTitle });
    // 后台 API（不阻塞 UI）
    try {
      await updateConversation(currentConversationId, { title: newTitle });
    } catch (error) {
      console.error('重命名失败:', error);
    }
  };

  // 侧边栏重命名回调：同步更新顶部标题
  const handleConversationRename = (id: string, newTitle: string) => {
    if (id === currentConversationId) {
      setConversationTitle(newTitle);
    }
  };

  // 删除对话回调：清除当前对话
  const handleConversationDelete = (id: string) => {
    if (id === currentConversationId) {
      // 跳转到主聊天页面（会触发状态清除）
      navigate('/chat');
    }
  };

  // 删除消息回调：更新侧边栏最后一条消息预览
  const handleMessageDelete = useCallback((_messageId: string, newLastMessage?: string) => {
    if (currentConversationId) {
      setConversationOptimisticUpdate({
        conversationId: currentConversationId,
        lastMessage: newLastMessage || '', // 如果没有消息了，显示空字符串
      });
    }
  }, [currentConversationId]);

  // 消息更新回调：更新侧边栏对话预览（用于重新生成等场景）
  const handleMessageUpdate = useCallback((newLastMessage: string) => {
    if (currentConversationId) {
      setConversationOptimisticUpdate({
        conversationId: currentConversationId,
        lastMessage: newLastMessage,
      });
    }
  }, [currentConversationId]);

  if (!isAuthenticated) {
    return null;
  }

  return (
    <div className="h-screen flex bg-gray-50">
      {/* 左侧栏 */}
      <Sidebar
        collapsed={sidebarCollapsed}
        onToggle={toggleSidebar}
        currentConversationId={currentConversationId}
        onNewConversation={handleNewConversation}
        onSelectConversation={handleSelectConversation}
        userCredits={user?.credits ?? 0}
        optimisticUpdate={conversationOptimisticUpdate}
        optimisticTitleUpdate={conversationOptimisticTitleUpdate}
        optimisticNewConversation={conversationOptimisticNew}
        onRename={handleConversationRename}
        onDelete={handleConversationDelete}
      />

      {/* 主内容区 */}
      <div className="flex-1 flex flex-col min-w-0">
        {/* 顶部导航栏 */}
        <header className="h-14 bg-white flex items-center justify-between px-4 flex-shrink-0">
          <div className="flex items-center space-x-3">
            {sidebarCollapsed && (
              <button
                onClick={toggleSidebar}
                className="p-2 hover:bg-gray-100 rounded-lg transition-colors"
                title="展开侧边栏"
              >
                <svg
                  className="w-5 h-5 text-gray-600"
                  fill="none"
                  viewBox="0 0 24 24"
                  stroke="currentColor"
                >
                  <path
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    strokeWidth={2}
                    d="M4 6h16M4 12h16M4 18h16"
                  />
                </svg>
              </button>
            )}
            {isEditingTitle ? (
              <input
                type="text"
                value={editingTitle}
                onChange={(e) => setEditingTitle(e.target.value)}
                onBlur={handleTitleSubmit}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') handleTitleSubmit();
                  if (e.key === 'Escape') setIsEditingTitle(false);
                }}
                autoFocus
                className="text-lg font-medium bg-gray-100 px-2 py-1 rounded outline-none focus:ring-2 focus:ring-blue-500"
              />
            ) : (
              <h1
                onDoubleClick={handleTitleDoubleClick}
                className="text-lg font-medium text-gray-900 truncate max-w-md cursor-pointer hover:text-blue-600"
                title="双击编辑标题"
              >
                {conversationTitle}
              </h1>
            )}
          </div>
          <div className="flex items-center space-x-2 text-sm text-gray-600">
            <span>剩余积分:</span>
            <span className="font-medium text-blue-600">{user?.credits ?? 0}</span>
          </div>
        </header>

        {/* 消息区域 */}
        <MessageArea
          conversationId={currentConversationId}
          modelId={conversationModelId}
          selectedModel={currentSelectedModel}
          onDelete={handleMessageDelete}
          onMessageUpdate={handleMessageUpdate}
        />

        {/* 输入框区域 */}
        <InputArea
          conversationId={currentConversationId}
          conversationModelId={conversationModelId}
          onConversationCreated={handleConversationCreated}
          onMessagePending={handleMessagePending}
          onMessageSent={handleMessageSent}
          onStreamContent={handleStreamContent}
          onStreamStart={handleStreamStart}
          onModelChange={setCurrentSelectedModel}
        />
      </div>
    </div>
  );
}
