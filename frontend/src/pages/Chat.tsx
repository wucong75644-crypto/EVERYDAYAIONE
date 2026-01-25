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
import Sidebar from '../components/chat/Sidebar';
import MessageArea from '../components/chat/MessageArea';
import InputArea from '../components/chat/InputArea';
import { updateConversation, getConversation } from '../services/conversation';
import type { Message } from '../services/message';

export default function Chat() {
  const navigate = useNavigate();
  const { id: urlConversationId } = useParams<{ id?: string }>();
  const { isAuthenticated, user, refreshUser } = useAuthStore();
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [currentConversationId, setCurrentConversationId] = useState<string | null>(null);
  const [conversationTitle, setConversationTitle] = useState('新对话');
  const [conversationModelId, setConversationModelId] = useState<string | null>(null);

  // 刷新触发器（用于触发子组件重新加载数据）
  const [conversationRefreshTrigger] = useState(0);
  const [messageRefreshTrigger] = useState(0);

  // 乐观更新
  const [newMessage, setNewMessage] = useState<Message | null>(null);
  const [isWaitingForAI, setIsWaitingForAI] = useState(false);

  // 流式内容状态
  const [streamingContent, setStreamingContent] = useState('');

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

  // 页面初始化时加载当前对话的模型ID（用于刷新页面后恢复模型选择）
  useEffect(() => {
    if (currentConversationId && conversationModelId === null) {
      // 只在 conversationModelId 还未加载时执行
      getConversation(currentConversationId)
        .then((conversation) => {
          setConversationModelId(conversation.model_id);
        })
        .catch((error) => {
          console.error('初始化加载对话详情失败:', error);
        });
    }
  }, [currentConversationId, conversationModelId]);

  // 监听 URL 参数变化，同步到状态
  useEffect(() => {
    // 立即清除消息状态（在对话ID变化之前）
    setNewMessage(null);
    setStreamingContent('');
    setIsWaitingForAI(false);

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

  // 消息开始发送（乐观更新）
  const handleMessagePending = useCallback((message: Message) => {
    setNewMessage(message);
    setIsWaitingForAI(true);
    // 立即更新对话列表排序
    if (currentConversationId) {
      setConversationOptimisticUpdate({
        conversationId: currentConversationId,
        lastMessage: message.content,
      });
    }
  }, [currentConversationId]);

  // 消息发送成功（接收 AI 回复）
  const handleMessageSent = useCallback((aiMessage?: Message | null) => {
    // 停止 AI 等待状态
    setIsWaitingForAI(false);
    // 清空流式内容
    setStreamingContent('');
    // 如果有 AI 回复，追加到列表
    if (aiMessage) {
      setNewMessage(aiMessage);
    } else {
      setNewMessage(null);
    }
    // 刷新用户积分
    refreshUser();
    // 已有 optimisticUpdate 机制会立即更新侧边栏，无需再调用 API
  }, [refreshUser]);

  // 流式内容更新
  const handleStreamContent = useCallback((text: string) => {
    setStreamingContent((prev) => prev + text);
  }, []);

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
        refreshTrigger={conversationRefreshTrigger}
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
          refreshTrigger={messageRefreshTrigger}
          newMessage={newMessage}
          isWaitingForAI={isWaitingForAI}
          streamingContent={streamingContent}
          modelId={conversationModelId}
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
        />
      </div>
    </div>
  );
}
