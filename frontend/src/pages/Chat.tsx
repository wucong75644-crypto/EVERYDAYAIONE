/**
 * 聊天页面
 *
 * 主要功能：
 * - 与 AI 对话生成图片/视频
 * - 查看历史对话
 * - 管理对话（重命名、删除）
 */

import { useState, useEffect, useCallback } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { useAuthStore } from '../stores/useAuthStore';
import { useTaskStore } from '../stores/useTaskStore';
import { useConversationRuntimeStore } from '../stores/useConversationRuntimeStore';
import Sidebar from '../components/chat/Sidebar';
import MessageArea from '../components/chat/MessageArea';
import InputArea from '../components/chat/InputArea';
import { ChatHeader } from '../components/chat/ChatHeader';
import { getConversation } from '../services/conversation';
import { CONVERSATIONS_CACHE_KEY } from '../components/chat/conversationUtils';
import { useMessageCallbacks } from '../hooks/useMessageCallbacks';
import { useConversationNavigation } from '../hooks/useConversationNavigation';
import { restoreAllPendingTasks } from '../utils/taskRestoration';
import type { UnifiedModel } from '../constants/models';

export default function Chat() {
  const navigate = useNavigate();
  const { id: urlConversationId } = useParams<{ id?: string }>();
  const { user, refreshUser } = useAuthStore();

  // 基础状态
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [currentConversationId, setCurrentConversationId] = useState<string | null>(null);
  const [conversationTitle, setConversationTitle] = useState('新对话');
  const [conversationModelId, setConversationModelId] = useState<string | null>(null);
  const [currentSelectedModel, setCurrentSelectedModel] = useState<UnifiedModel | null>(null);

  // 使用消息回调 Hook
  const {
    handleMessagePending,
    handleMessageSent,
    handleStreamStart,
    handleStreamContent,
    conversationOptimisticUpdate,
    setConversationOptimisticUpdate,
  } = useMessageCallbacks({
    conversationTitle,
    currentConversationId,
  });

  // 使用对话导航 Hook
  const {
    handleNewConversation,
    handleSelectConversation,
    handleConversationCreated,
    handleConversationRename,
    handleConversationDelete,
    conversationOptimisticTitleUpdate,
    conversationOptimisticNew,
    isEditingTitle,
    editingTitle,
    setEditingTitle,
    handleTitleDoubleClick,
    handleTitleSubmit,
    cancelTitleEdit,
  } = useConversationNavigation({
    currentConversationId,
    setCurrentConversationId,
    conversationTitle,
    setConversationTitle,
  });

  // 页面加载时刷新用户信息（包括积分）
  // 由于页面已被 ProtectedRoute 保护，此处无需检查登录状态
  useEffect(() => {
    refreshUser();
  }, [refreshUser]);

  // 恢复进行中的任务（页面刷新/登录后）
  useEffect(() => {
    if (!user) return;

    const timer = setTimeout(() => {
      restoreAllPendingTasks();
    }, 1000);

    return () => clearTimeout(timer);
  }, [user]);

  // 监听 URL 参数变化，同步到状态
  useEffect(() => {
    // LRU 清理：保留当前对话 + 活跃任务对话 + 正在生成的对话
    if (urlConversationId) {
      const runtimeState = useConversationRuntimeStore.getState();
      const activeTaskIds = useTaskStore.getState().getActiveConversationIds();

      // 查找所有正在生成（streaming）的对话，避免删除它们的状态
      const activeStreamingIds: string[] = [];
      runtimeState.states.forEach((state, conversationId) => {
        if (state.isGenerating || state.streamingMessageId) {
          activeStreamingIds.push(conversationId);
        }
      });

      // 合并所有需要保留的对话 ID（去重后保留前 10 个）
      const keepIds = Array.from(
        new Set([urlConversationId, ...activeTaskIds, ...activeStreamingIds])
      ).slice(0, 10);

      runtimeState.cleanup(keepIds);
    }

    if (urlConversationId) {
      // 立即设置对话 ID（无需等待 API，让 MessageArea 立即加载缓存）
      // eslint-disable-next-line react-hooks/set-state-in-effect -- URL 参数同步到状态是合理用例
      setCurrentConversationId(urlConversationId);

      // 优先从 localStorage 缓存中获取标题（避免显示"加载中..."）
      let cachedTitle: string | null = null;
      try {
        const cached = localStorage.getItem(CONVERSATIONS_CACHE_KEY);
        if (cached) {
          const conversations = JSON.parse(cached) as { id: string; title: string }[];
          const found = conversations.find((c) => c.id === urlConversationId);
          if (found) cachedTitle = found.title;
        }
      } catch {
        // 解析失败，忽略
      }
      setConversationTitle(cachedTitle || '加载中...');

      // 异步加载对话详情（更新 title 和 modelId，确保数据最新）
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

  // 切换侧边栏
  const toggleSidebar = useCallback(() => {
    setSidebarCollapsed((prev) => !prev);
  }, []);

  // 删除消息回调：更新侧边栏最后一条消息预览
  const handleMessageDelete = useCallback(
    (_messageId: string, newLastMessage?: string) => {
      if (currentConversationId) {
        setConversationOptimisticUpdate({
          conversationId: currentConversationId,
          lastMessage: newLastMessage || '',
        });
      }
    },
    [currentConversationId, setConversationOptimisticUpdate]
  );

  // 消息更新回调：更新侧边栏对话预览（用于重新生成等场景）
  const handleMessageUpdate = useCallback(
    (newLastMessage: string) => {
      if (currentConversationId) {
        setConversationOptimisticUpdate({
          conversationId: currentConversationId,
          lastMessage: newLastMessage,
        });
      }
    },
    [currentConversationId, setConversationOptimisticUpdate]
  );

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
        <ChatHeader
          sidebarCollapsed={sidebarCollapsed}
          onToggleSidebar={toggleSidebar}
          conversationTitle={conversationTitle}
          isEditingTitle={isEditingTitle}
          editingTitle={editingTitle}
          onEditingTitleChange={setEditingTitle}
          onTitleDoubleClick={handleTitleDoubleClick}
          onTitleSubmit={handleTitleSubmit}
          onTitleCancel={cancelTitleEdit}
          userCredits={user?.credits ?? 0}
        />

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
