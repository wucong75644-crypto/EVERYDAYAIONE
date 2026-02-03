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
import { useUnifiedMessages } from '../hooks/useUnifiedMessages';
import { useChatStore } from '../stores/useChatStore';
import type { Message } from '../services/message';
import { performanceMonitor } from '../utils/performanceMonitor';

// 用户信息刷新间隔（5 分钟）
const USER_REFRESH_INTERVAL = 5 * 60 * 1000;
const USER_REFRESH_KEY = 'everydayai_user_refresh_time';

/**
 * 从缓存预读初始对话 ID
 * 优先从 URL 读取，否则从 localStorage 缓存读取第一个对话
 * 实现消息加载并行化，避免等待对话列表返回
 */
function getInitialConversationId(): string | null {
  // 优先从 URL 读取
  const match = window.location.pathname.match(/\/chat\/([^/]+)/);
  if (match) return match[1];

  // 从缓存预读第一个对话
  try {
    const cached = localStorage.getItem(CONVERSATIONS_CACHE_KEY);
    if (cached) {
      const list = JSON.parse(cached);
      return list[0]?.id || null;
    }
  } catch {
    // 解析失败，忽略
  }
  return null;
}

export default function Chat() {
  const navigate = useNavigate();
  const { id: urlConversationId } = useParams<{ id?: string }>();
  const { user, refreshUser } = useAuthStore();

  // 性能监控：标记组件挂载时间
  const ttiMeasured = useRef(false);
  useEffect(() => {
    performanceMonitor.mark('chat_mount');
    return () => {
      // 组件卸载时输出性能报告
      performanceMonitor.report();
    };
  }, []);

  // 基础状态
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  // 从缓存预读初始对话 ID，实现消息并行加载
  const [currentConversationId, setCurrentConversationId] = useState<string | null>(getInitialConversationId);
  const [conversationTitle, setConversationTitle] = useState('新对话');
  const [conversationModelId, setConversationModelId] = useState<string | null>(null);
  const [currentSelectedModel, setCurrentSelectedModel] = useState<UnifiedModel | null>(null);

  // 请求序号（用于防止快速切换对话时的竞态）
  const conversationRequestSeqRef = useRef(0);

  // 获取统一消息列表（用于 setMessages 兼容层）
  const mergedMessages = useUnifiedMessages(currentConversationId);
  const { replaceMessage, appendMessage } = useChatStore();

  // 性能监控：测量 TTI（首次消息加载完成时）
  useEffect(() => {
    if (ttiMeasured.current) return;
    if (mergedMessages.length > 0 || !currentConversationId) {
      ttiMeasured.current = true;
      performanceMonitor.measure('TTI', 'chat_mount');
    }
  }, [mergedMessages.length, currentConversationId]);

  // 创建 setMessages 兼容层（用于统一缓存写入）
  const setMessages = useCallback(
    (updater: Message[] | ((prev: Message[]) => Message[])) => {
      if (typeof updater === 'function') {
        const newMessages = updater(mergedMessages);

        // 使用Map提升性能，O(1)查找，避免index错位
        const oldMessagesMap = new Map(mergedMessages.map(m => [m.id, m]));

        // ✅ 方案A：按对话ID分组消息，避免跨对话写入
        const messagesByConversation = new Map<string, { toReplace: Array<{ oldId: string; newMsg: Message }>; toAppend: Message[] }>();

        newMessages.forEach((newMsg) => {
          const conversationId = newMsg.conversation_id;
          if (!conversationId) return; // 忽略无效消息

          // 初始化对话分组
          if (!messagesByConversation.has(conversationId)) {
            messagesByConversation.set(conversationId, { toReplace: [], toAppend: [] });
          }

          const group = messagesByConversation.get(conversationId)!;
          const oldMsg = oldMessagesMap.get(newMsg.id);

          if (oldMsg && oldMsg !== newMsg) {
            // 消息被修改 → 记录待替换
            group.toReplace.push({ oldId: oldMsg.id, newMsg });
          } else if (!oldMsg && newMsg && !newMsg.id.startsWith('temp-') && !newMsg.id.startsWith('streaming-')) {
            // 新增持久化消息 → 记录待追加
            group.toAppend.push(newMsg);
          }
        });

        // 遍历每个对话，写入对应的缓存
        messagesByConversation.forEach((group, conversationId) => {
          group.toReplace.forEach(({ oldId, newMsg }) => {
            replaceMessage(conversationId, oldId, newMsg);
          });
          group.toAppend.forEach((msg) => {
            appendMessage(conversationId, msg);
          });
        });
      }
    },
    [mergedMessages, replaceMessage, appendMessage]
  );

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
    setMessages,
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
  // 条件调用：5 分钟内刷新过则跳过，避免重复请求
  useEffect(() => {
    const lastRefresh = localStorage.getItem(USER_REFRESH_KEY);
    const now = Date.now();

    // 5 分钟内刷新过，跳过
    if (lastRefresh && now - parseInt(lastRefresh) < USER_REFRESH_INTERVAL) {
      return;
    }

    refreshUser().then(() => {
      localStorage.setItem(USER_REFRESH_KEY, String(Date.now()));
    });
  }, [refreshUser]);

  // 恢复进行中的任务（页面刷新/登录后）
  // 条件触发：等待对话列表加载完成后执行，避免固定 1000ms 延迟
  const conversations = useChatStore((state) => state.conversations);
  const taskRestoreTriggered = useRef(false);

  useEffect(() => {
    if (!user) return;
    if (taskRestoreTriggered.current) return;

    // 检查是否有缓存的对话列表
    const hasCachedConversations = (() => {
      try {
        const cached = localStorage.getItem(CONVERSATIONS_CACHE_KEY);
        return cached ? JSON.parse(cached).length > 0 : false;
      } catch {
        return false;
      }
    })();

    // 触发条件：store 有数据，或缓存为空（新用户）
    if (conversations.length > 0 || !hasCachedConversations) {
      taskRestoreTriggered.current = true;
      restoreAllPendingTasks();
    }
  }, [user, conversations.length]);

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

      // 合并所有需要保留的对话 ID（去重后保留前 15 个）
      const keepIds = Array.from(
        new Set([urlConversationId, ...activeTaskIds, ...activeStreamingIds])
      ).slice(0, 15);

      runtimeState.cleanup(keepIds);
    }

    if (urlConversationId) {
      // 递增请求序号（用于检测过期响应）
      conversationRequestSeqRef.current += 1;
      const currentSeq = conversationRequestSeqRef.current;

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
          // 检测过期响应：如果请求序号已变化，说明用户已切换到其他对话，丢弃响应
          if (currentSeq !== conversationRequestSeqRef.current) {
            return;  // 丢弃过期响应
          }
          // 只有当前 URL 还是这个对话时才更新（避免快速切换导致的状态错乱）
          if (urlConversationId === conversation.id) {
            setConversationTitle(conversation.title);
            setConversationModelId(conversation.model_id);
          }
        })
        .catch((error) => {
          // 检测过期响应
          if (currentSeq !== conversationRequestSeqRef.current) {
            return;  // 丢弃过期错误
          }
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
