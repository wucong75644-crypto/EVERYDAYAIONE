/**
 * 聊天页面
 *
 * 主要功能：
 * - 与 AI 对话生成图片/视频
 * - 查看历史对话
 * - 管理对话（重命名、删除）
 */

import { useState, useEffect, useCallback, useRef } from 'react';
import { useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { useAuthStore } from '../stores/useAuthStore';
import { useMessageStore } from '../stores/useMessageStore';
import Sidebar from '../components/chat/layout/Sidebar';
import InvitationNotice from '../components/admin/InvitationNotice';
import MessageArea from '../components/chat/message/MessageArea';
import InputArea from '../components/chat/input/InputArea';
import { ChatHeader } from '../components/chat/layout/ChatHeader';
import SearchPanel from '../components/chat/search/SearchPanel';
import ScheduledTaskPanel from '../components/scheduled-tasks/ScheduledTaskPanel';
import WorkspaceView from '../components/workspace/WorkspaceView';
import type { WorkspaceFile } from '../services/workspace';
import { PageTransition } from '../components/motion';
import { getConversation } from '../services/conversation';
import { CONVERSATIONS_CACHE_KEY } from '../components/chat/layout/conversationUtils';
import { useMessageCallbacks } from '../hooks/useMessageCallbacks';
import { useConversationNavigation } from '../hooks/useConversationNavigation';
import type { UnifiedModel } from '../constants/models';
import { useUnifiedMessages } from '../hooks/useUnifiedMessages';
import { performanceMonitor } from '../utils/performanceMonitor';
import { tabSync } from '../utils/tabSync';
import { logger } from '../utils/logger';

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
  const [searchParams, setSearchParams] = useSearchParams();
  const { user, refreshUser } = useAuthStore();
  const currentOrg = useAuthStore((s) => s.currentOrg);

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
  // 视图切换：chat（对话）/ workspace（工作区文件浏览器）
  const [view, setView] = useState<'chat' | 'workspace'>('chat');
  // prompt 状态提升（从 InputArea 移到 Chat.tsx，避免切换 view 时丢失用户输入）
  const [prompt, setPrompt] = useState('');
  // 工作区文件待发送队列（"插入到聊天"功能）
  const [pendingWorkspaceFiles, setPendingWorkspaceFiles] = useState<WorkspaceFile[]>([]);
  // 工作区面板宽度（可拖拽调整）
  const [workspacePanelWidth, setWorkspacePanelWidth] = useState(480);
  // 从缓存预读初始对话 ID，实现消息并行加载
  const [currentConversationId, setCurrentConversationId] = useState<string | null>(getInitialConversationId);
  const [conversationTitle, setConversationTitle] = useState('新对话');
  const [conversationModelId, setConversationModelId] = useState<string | null>(null);
  // 当前选择的模型（由 InputArea 设置，用于将来扩展）
  const [, setCurrentSelectedModel] = useState<UnifiedModel | null>(null);
  // 搜索面板开关（V3 Phase 4：cursor 分页 + 搜索）
  const [searchPanelOpen, setSearchPanelOpen] = useState(false);
  // 定时任务面板开关
  const [scheduledTaskPanelOpen, setScheduledTaskPanelOpen] = useState(false);

  // Cmd+F / Ctrl+F 全局快捷键打开搜索面板
  useEffect(() => {
    if (!currentConversationId) return;
    const handleKeyDown = (e: KeyboardEvent) => {
      // 仅在 meta+F (Mac) 或 ctrl+F (Win/Linux) 时拦截
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'f') {
        e.preventDefault();
        setSearchPanelOpen(true);
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [currentConversationId]);

  // 请求序号（用于防止快速切换对话时的竞态）
  const conversationRequestSeqRef = useRef(0);

  // 获取统一消息列表
  const mergedMessages = useUnifiedMessages(currentConversationId);

  // 性能监控：测量 TTI（首次消息加载完成时）
  useEffect(() => {
    if (ttiMeasured.current) return;
    if (mergedMessages.length > 0 || !currentConversationId) {
      ttiMeasured.current = true;
      performanceMonitor.measure('TTI', 'chat_mount');
    }
  }, [mergedMessages.length, currentConversationId]);

  // 使用消息回调 Hook
  const {
    handleMessagePending,
    handleMessageSent,
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

  // 从 URL query 读取 model 参数（从模型广场跳转过来）
  useEffect(() => {
    const modelParam = searchParams.get('model');
    if (modelParam) {
      // 设置为对话模型，让 InputArea 的 useModelSelection 自动选中
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setConversationModelId(modelParam);
      // 清除 query 参数，避免刷新重复触发
      searchParams.delete('model');
      setSearchParams(searchParams, { replace: true });
    }
  }, [searchParams, setSearchParams]);

  // 监听跨标签页同步事件
  useEffect(() => {
    // 其他标签页完成聊天任务：刷新当前对话的消息列表
    const unsubscribeCompleted = tabSync.on('chat_completed', (data) => {
      if (data.conversationId === currentConversationId) {
        // 清除缓存，触发 MessageArea 重新加载
        useMessageStore.getState().clearConversationCache(data.conversationId);
      }
    });

    // 其他标签页聊天失败：同上
    const unsubscribeFailed = tabSync.on('chat_failed', (data) => {
      if (data.conversationId === currentConversationId) {
        useMessageStore.getState().clearConversationCache(data.conversationId);
      }
    });

    // 其他标签页积分变动：刷新用户信息
    const unsubscribeCredits = tabSync.on('credits_changed', () => {
      refreshUser();
    });

    // 其他标签页删除对话：如果是当前对话，跳转回主页
    const unsubscribeDeleted = tabSync.on('conversation_deleted', (data) => {
      if (data.conversationId === currentConversationId) {
        navigate('/chat');
      }
    });

    return () => {
      unsubscribeCompleted();
      unsubscribeFailed();
      unsubscribeCredits();
      unsubscribeDeleted();
    };
  }, [currentConversationId, refreshUser, navigate]);

  // 监听 URL 参数变化，同步到状态
  useEffect(() => {
    // LRU 清理：保留当前对话 + 活跃任务对话 + 正在生成的对话
    if (urlConversationId) {
      const store = useMessageStore.getState();
      const activeTaskIds = store.getActiveConversationIds();

      // 查找所有正在生成（streaming）的对话，避免删除它们的状态
      const activeStreamingIds: string[] = [];
      store.streamingMessages.forEach((_, conversationId) => {
        activeStreamingIds.push(conversationId);
      });

      // 合并所有需要保留的对话 ID（去重后保留前 15 个）
      const keepIds = Array.from(
        new Set([urlConversationId, ...activeTaskIds, ...activeStreamingIds])
      ).slice(0, 15);

      store.cleanup(keepIds);
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
          // 只有对话确实不存在（404）才跳转，其他错误（网络抖动、超时等）不跳走
          const status = error?.response?.status;
          if (status === 404) {
            logger.error('chat', '对话不存在', undefined, { conversationId: urlConversationId });
            navigate('/chat');
          } else {
            logger.error('chat', '加载对话失败（非 404，不跳转）', error, { status });
          }
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

  // 切换工作区（toggle）— 不自动收起侧边栏，让用户自己决定
  const handleToggleWorkspace = useCallback(() => {
    setView((prev) => prev === 'chat' ? 'workspace' : 'chat');
  }, []);

  // 工作区："插入到聊天"回调（按 workspace_path 去重，不自动关闭工作区）
  const handleSendFromWorkspace = useCallback((file: WorkspaceFile) => {
    setPendingWorkspaceFiles((prev) =>
      prev.some((f) => f.workspace_path === file.workspace_path) ? prev : [...prev, file],
    );
    // 并排模式下不自动关闭工作区，用户可以继续选文件
  }, []);

  // 移除单个待发送的 workspace 文件
  const handleRemoveWorkspaceFile = useCallback((workspacePath: string) => {
    setPendingWorkspaceFiles((prev) => prev.filter((f) => f.workspace_path !== workspacePath));
  }, []);

  // 发送后清空 workspace 文件队列
  const handleWorkspaceFilesConsumed = useCallback(() => {
    setPendingWorkspaceFiles([]);
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

  // 搜索结果跳转到指定消息（Phase 5 实现具体滚动+闪烁逻辑）
  // 当前先派发自定义事件，由 MessageArea 监听并响应
  const handleJumpToMessage = useCallback(
    (messageId: string) => {
      // 派发全局事件，MessageArea 在 Phase 5 接管后会订阅
      window.dispatchEvent(
        new CustomEvent('chat:jump-to-message', { detail: { messageId } }),
      );
    },
    [],
  );

  return (
    <PageTransition className="h-screen flex bg-surface">
      {/* 邀请通知 */}
      <InvitationNotice />

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

      {/* 主内容区：用 CSS calc 显式计算宽度（VS Code 模式，不依赖 flex 自动收缩） */}
      <div className="flex-1 flex min-w-0 relative">
        {/* 对话区 — 显式宽度计算 */}
        <div
          className="flex flex-col overflow-hidden"
          style={{
            width: view === 'workspace'
              ? `calc(100% - ${workspacePanelWidth}px - 4px)`
              : '100%',
          }}
        >
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
            onOpenSearch={
              currentConversationId ? () => setSearchPanelOpen(true) : undefined
            }
            onOpenScheduledTasks={
              currentOrg
                ? () => setScheduledTaskPanelOpen(true)
                : undefined
            }
          />

          {/* 消息区域 */}
          <MessageArea
            conversationId={currentConversationId}
            onDelete={handleMessageDelete}
            compact={view === 'workspace'}
          />

          {/* 输入框区域 */}
          <InputArea
            conversationId={currentConversationId}
            conversationModelId={conversationModelId}
            onConversationCreated={handleConversationCreated}
            onMessagePending={handleMessagePending}
            onMessageSent={handleMessageSent}
            onModelChange={setCurrentSelectedModel}
            prompt={prompt}
            onPromptChange={setPrompt}
            workspaceFiles={pendingWorkspaceFiles}
            onRemoveWorkspaceFile={handleRemoveWorkspaceFile}
            onWorkspaceFilesConsumed={handleWorkspaceFilesConsumed}
            onOpenWorkspace={handleToggleWorkspace}
            workspaceOpen={view === 'workspace'}
            compact={view === 'workspace'}
          />
        </div>

        {/* 可拖拽分割线 + 工作区面板（右侧） */}
        {view === 'workspace' && (
          <>
            {/* 拖拽分割线 */}
            <div
              className="w-1 cursor-col-resize bg-[var(--s-border-default)] hover:bg-[var(--s-accent)] active:bg-[var(--s-accent)] transition-colors flex-shrink-0"
              onMouseDown={(e) => {
                e.preventDefault();
                const startX = e.clientX;
                const startWidth = workspacePanelWidth;
                const container = (e.currentTarget.parentElement as HTMLElement);
                const containerWidth = container.offsetWidth;
                const onMouseMove = (ev: MouseEvent) => {
                  const delta = startX - ev.clientX;
                  // 工作区宽度范围：320px ~ 容器宽度的70%
                  const maxW = Math.floor(containerWidth * 0.7);
                  const newWidth = Math.max(320, Math.min(maxW, startWidth + delta));
                  setWorkspacePanelWidth(newWidth);
                };
                const onMouseUp = () => {
                  document.removeEventListener('mousemove', onMouseMove);
                  document.removeEventListener('mouseup', onMouseUp);
                  document.body.style.cursor = '';
                  document.body.style.userSelect = '';
                };
                document.body.style.cursor = 'col-resize';
                document.body.style.userSelect = 'none';
                document.addEventListener('mousemove', onMouseMove);
                document.addEventListener('mouseup', onMouseUp);
              }}
              title="拖拽调整宽度"
            />

            {/* 工作区面板 — 显式宽度 */}
            <div
              className="flex flex-col overflow-hidden flex-shrink-0"
              style={{ width: workspacePanelWidth }}
            >
              <WorkspaceView
                onBack={() => setView('chat')}
                onSendToChat={handleSendFromWorkspace}
              />
            </div>
          </>
        )}
      </div>

      {/* 消息搜索面板（V3 Phase 4：cursor 分页 + 搜索方案）
          - ChatHeader 🔍 按钮触发
          - Cmd+F / Ctrl+F 全局快捷键触发
          - ESC 关闭
          - 仅在有当前对话时挂载（防止无对话状态打开搜索） */}
      {currentConversationId && (
        <SearchPanel
          isOpen={searchPanelOpen}
          onClose={() => setSearchPanelOpen(false)}
          conversationId={currentConversationId}
          onJumpToMessage={handleJumpToMessage}
        />
      )}

      {/* 定时任务面板 */}
      <ScheduledTaskPanel
        isOpen={scheduledTaskPanelOpen}
        onClose={() => setScheduledTaskPanelOpen(false)}
      />
    </PageTransition>
  );
}
