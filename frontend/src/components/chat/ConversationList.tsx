/**
 * 对话列表组件
 *
 * 按日期分组显示历史对话
 */

import { useState, useEffect, useCallback, useRef } from 'react';
import {
  getConversationList,
  deleteConversation,
  updateConversation,
  type ConversationListItem,
} from '../../services/conversation';

/** 乐观更新参数 */
interface OptimisticUpdate {
  conversationId: string;
  lastMessage: string;
}

/** 标题乐观更新参数 */
interface OptimisticTitleUpdate {
  id: string;
  title: string;
}

/** 新对话乐观更新参数 */
interface OptimisticNewConversation {
  id: string;
  title: string;
}

interface ConversationListProps {
  currentConversationId: string | null;
  onSelectConversation: (id: string, title: string, modelId?: string | null) => void;
  refreshTrigger?: number;
  /** 乐观更新：立即将指定对话移到最前并更新信息 */
  optimisticUpdate?: OptimisticUpdate | null;
  /** 标题乐观更新：立即更新指定对话的标题 */
  optimisticTitleUpdate?: OptimisticTitleUpdate | null;
  /** 新对话乐观更新：创建新对话时立即添加到列表顶部 */
  optimisticNewConversation?: OptimisticNewConversation | null;
  /** 重命名回调：通知父组件更新标题 */
  onRename?: (id: string, newTitle: string) => void;
  /** 删除回调：通知父组件清除当前对话 */
  onDelete?: (id: string) => void;
  /** 搜索关键词 */
  searchQuery?: string;
}

// 格式化日期分组
function formatDateGroup(dateStr: string): string {
  const date = new Date(dateStr);
  const today = new Date();
  const yesterday = new Date(today);
  yesterday.setDate(yesterday.getDate() - 1);

  if (date.toDateString() === today.toDateString()) {
    return '今天';
  } else if (date.toDateString() === yesterday.toDateString()) {
    return '昨天';
  } else {
    return `${date.getMonth() + 1}月${date.getDate()}日`;
  }
}

// 按日期分组对话
function groupConversationsByDate(
  conversations: ConversationListItem[]
): Record<string, ConversationListItem[]> {
  const groups: Record<string, ConversationListItem[]> = {};

  conversations.forEach((conv) => {
    const group = formatDateGroup(conv.updated_at);
    if (!groups[group]) {
      groups[group] = [];
    }
    groups[group].push(conv);
  });

  return groups;
}

// localStorage 缓存键
const CONVERSATIONS_CACHE_KEY = 'everydayai_conversations_cache';

export default function ConversationList({
  currentConversationId,
  onSelectConversation,
  refreshTrigger = 0,
  optimisticUpdate = null,
  optimisticTitleUpdate = null,
  optimisticNewConversation = null,
  onRename,
  onDelete,
  searchQuery = '',
}: ConversationListProps) {
  // 点击反馈状态（立即响应用户点击）
  const [clickedId, setClickedId] = useState<string | null>(null);

  // 从缓存初始化对话列表（刷新时立即显示缓存数据）
  const [conversations, setConversations] = useState<ConversationListItem[]>(() => {
    try {
      const cached = localStorage.getItem(CONVERSATIONS_CACHE_KEY);
      return cached ? JSON.parse(cached) : [];
    } catch {
      return [];
    }
  });

  // 只有在没有缓存数据时才显示骨架屏
  const [loading, setLoading] = useState(() => {
    try {
      const cached = localStorage.getItem(CONVERSATIONS_CACHE_KEY);
      return !cached; // 有缓存就不显示加载状态
    } catch {
      return true;
    }
  });

  const [contextMenu, setContextMenu] = useState<{
    id: string;
    title: string;
    x: number;
    y: number;
  } | null>(null);
  const [renameId, setRenameId] = useState<string | null>(null);
  const [renameTitle, setRenameTitle] = useState('');
  const [deleteConfirm, setDeleteConfirm] = useState<{
    id: string;
    title: string;
  } | null>(null);

  // 是否已执行过自动选中（防止重复触发）
  const hasAutoSelected = useRef(false);
  // 保存回调引用，避免依赖变化导致重新加载
  const onSelectConversationRef = useRef(onSelectConversation);
  onSelectConversationRef.current = onSelectConversation;

  // 加载对话列表
  const loadConversations = useCallback(async (isInitial = false) => {
    try {
      // 仅在没有缓存数据时才显示骨架屏
      if (isInitial && conversations.length === 0) {
        setLoading(true);
      }

      const response = await getConversationList();
      setConversations(response.conversations);

      // 保存到 localStorage 缓存
      try {
        localStorage.setItem(CONVERSATIONS_CACHE_KEY, JSON.stringify(response.conversations));
      } catch (error) {
        console.warn('保存对话列表缓存失败:', error);
      }

      // 仅首次加载时自动选中最近的对话
      if (isInitial && !hasAutoSelected.current && response.conversations.length > 0) {
        hasAutoSelected.current = true;
        const mostRecent = response.conversations[0];
        onSelectConversationRef.current(mostRecent.id, mostRecent.title, mostRecent.model_id);
      }
    } catch (error) {
      console.error('加载对话列表失败:', error);
    } finally {
      if (isInitial) {
        setLoading(false);
      }
    }
  }, [conversations.length]);

  // 首次加载
  useEffect(() => {
    loadConversations(true);
  }, [loadConversations]);

  // refreshTrigger 变化时刷新（发送消息后）
  const isFirstRender = useRef(true);
  useEffect(() => {
    if (isFirstRender.current) {
      isFirstRender.current = false;
      return;
    }
    loadConversations(false);
  }, [refreshTrigger, loadConversations]);

  // 乐观更新：立即将指定对话移到最前并更新 last_message
  useEffect(() => {
    if (!optimisticUpdate) return;

    setConversations((prev) => {
      const target = prev.find((c) => c.id === optimisticUpdate.conversationId);
      if (!target) return prev;

      const updated = {
        ...target,
        last_message: optimisticUpdate.lastMessage,
        updated_at: new Date().toISOString(),
      };
      return [updated, ...prev.filter((c) => c.id !== optimisticUpdate.conversationId)];
    });
  }, [optimisticUpdate]);

  // 标题乐观更新：立即更新指定对话的标题
  useEffect(() => {
    if (!optimisticTitleUpdate) return;

    setConversations((prev) =>
      prev.map((c) =>
        c.id === optimisticTitleUpdate.id
          ? { ...c, title: optimisticTitleUpdate.title }
          : c
      )
    );
  }, [optimisticTitleUpdate]);

  // 新对话乐观更新：立即添加新对话到列表顶部
  useEffect(() => {
    if (!optimisticNewConversation) return;

    setConversations((prev) => {
      // 检查是否已存在（避免重复添加）
      if (prev.some((c) => c.id === optimisticNewConversation.id)) {
        return prev;
      }
      // 添加到列表顶部
      return [
        {
          id: optimisticNewConversation.id,
          title: optimisticNewConversation.title,
          last_message: '',
          model_id: null,
          updated_at: new Date().toISOString(),
        },
        ...prev,
      ];
    });
  }, [optimisticNewConversation]);

  // 右键菜单处理
  const handleContextMenu = (
    e: React.MouseEvent,
    id: string,
    title: string
  ) => {
    e.preventDefault();
    setContextMenu({ id, title, x: e.clientX, y: e.clientY });
  };

  // 关闭右键菜单
  const closeContextMenu = () => {
    setContextMenu(null);
  };

  // 删除对话（显示确认弹框）
  const handleDelete = (id: string) => {
    setDeleteConfirm({ id, title: contextMenu?.title || '' });
    closeContextMenu();
  };

  // 确认删除
  const confirmDelete = async () => {
    if (!deleteConfirm) return;
    try {
      await deleteConversation(deleteConfirm.id);
      const updatedConversations = conversations.filter((c) => c.id !== deleteConfirm.id);
      setConversations(updatedConversations);

      // 更新缓存
      try {
        localStorage.setItem(CONVERSATIONS_CACHE_KEY, JSON.stringify(updatedConversations));
      } catch (error) {
        console.warn('更新缓存失败:', error);
      }

      onDelete?.(deleteConfirm.id);
    } catch (error) {
      console.error('删除对话失败:', error);
    }
    setDeleteConfirm(null);
  };

  // 开始重命名
  const handleStartRename = (id: string, title: string) => {
    setRenameId(id);
    setRenameTitle(title);
    closeContextMenu();
  };

  // 提交重命名（乐观更新）
  const handleSubmitRename = async () => {
    if (!renameId || !renameTitle.trim()) {
      setRenameId(null);
      return;
    }

    const newTitle = renameTitle.trim();
    const oldId = renameId;

    // 乐观更新：先更新 UI
    const updatedConversations = conversations.map((c) =>
      c.id === oldId ? { ...c, title: newTitle } : c
    );
    setConversations(updatedConversations);

    // 更新缓存
    try {
      localStorage.setItem(CONVERSATIONS_CACHE_KEY, JSON.stringify(updatedConversations));
    } catch (error) {
      console.warn('更新缓存失败:', error);
    }

    onRename?.(oldId, newTitle);
    setRenameId(null);

    // 后台 API 请求
    try {
      await updateConversation(oldId, { title: newTitle });
    } catch (error) {
      console.error('重命名失败:', error);
    }
  };

  // 点击其他地方关闭菜单
  useEffect(() => {
    const handleClick = () => closeContextMenu();
    window.addEventListener('click', handleClick);
    return () => window.removeEventListener('click', handleClick);
  }, []);

  if (loading) {
    return (
      <div className="p-4 space-y-3">
        {[1, 2, 3].map((i) => (
          <div key={i} className="animate-pulse">
            <div className="h-4 bg-gray-200 rounded w-3/4 mb-2"></div>
            <div className="h-3 bg-gray-200 rounded w-1/2"></div>
          </div>
        ))}
      </div>
    );
  }

  // 根据搜索关键词过滤对话
  const filteredConversations = searchQuery
    ? conversations.filter((conv) =>
        conv.title.toLowerCase().includes(searchQuery.toLowerCase())
      )
    : conversations;

  if (filteredConversations.length === 0) {
    return (
      <div className="p-4 text-center text-gray-500 text-sm">
        {searchQuery ? '未找到匹配的对话' : '暂无对话记录'}
      </div>
    );
  }

  const groupedConversations = groupConversationsByDate(filteredConversations);

  return (
    <div className="py-2">
      {Object.entries(groupedConversations).map(([group, convs]) => (
        <div key={group} className="mb-2">
          {/* 日期分组标题 */}
          <div className="px-4 py-1.5 text-xs text-gray-400 font-normal uppercase tracking-wide">
            {group}
          </div>

          {/* 该分组下的对话 */}
          {convs.map((conv) => (
            <div
              key={conv.id}
              onClick={() => {
                if (renameId !== conv.id) {
                  // 立即设置点击状态（提供即时反馈）
                  setClickedId(conv.id);
                  onSelectConversation(conv.id, conv.title, conv.model_id);
                  // 短暂延迟后清除点击状态
                  setTimeout(() => setClickedId(null), 300);
                }
              }}
              onDoubleClick={() => handleStartRename(conv.id, conv.title)}
              onContextMenu={(e) => handleContextMenu(e, conv.id, conv.title)}
              className={`mx-2 mb-1 px-3 py-1.5 rounded-lg cursor-pointer transition-all duration-150 outline-none ${
                currentConversationId === conv.id
                  ? 'bg-blue-50'
                  : clickedId === conv.id
                  ? 'bg-blue-50 scale-[0.98]'
                  : 'hover:bg-gray-50'
              }`}
            >
              {renameId === conv.id ? (
                <input
                  type="text"
                  value={renameTitle}
                  onChange={(e) => setRenameTitle(e.target.value)}
                  onBlur={handleSubmitRename}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') {
                      handleSubmitRename();
                    } else if (e.key === 'Escape') {
                      setRenameId(null);
                    }
                  }}
                  autoFocus
                  className="w-full bg-white text-gray-900 text-sm px-2 py-1 rounded outline-none border border-blue-500 focus:ring-2 focus:ring-blue-300"
                  onClick={(e) => e.stopPropagation()}
                />
              ) : (
                <>
                  <div
                    className={`text-sm truncate ${
                      currentConversationId === conv.id
                        ? 'text-gray-900 font-semibold'
                        : 'text-gray-800 font-medium'
                    }`}
                  >
                    {conv.title}
                  </div>
                  {conv.last_message && (
                    <div className="text-xs text-gray-500 truncate mt-1">
                      {conv.last_message}
                    </div>
                  )}
                </>
              )}
            </div>
          ))}
        </div>
      ))}

      {/* 右键菜单 */}
      {contextMenu && (
        <div
          className="fixed bg-white rounded-lg shadow-lg border border-gray-200 py-1 z-50 min-w-32"
          style={{ left: contextMenu.x, top: contextMenu.y }}
          onClick={(e) => e.stopPropagation()}
        >
          <button
            onClick={() => handleStartRename(contextMenu.id, contextMenu.title)}
            className="w-full px-4 py-2 text-left text-sm text-gray-700 hover:bg-gray-100"
          >
            重命名
          </button>
          <button
            onClick={() => handleDelete(contextMenu.id)}
            className="w-full px-4 py-2 text-left text-sm text-red-600 hover:bg-gray-100"
          >
            删除
          </button>
        </div>
      )}

      {/* 删除确认弹框 */}
      {deleteConfirm && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
          <div className="bg-white rounded-xl p-6 w-80 shadow-xl relative">
            <div className="flex items-start gap-3">
              <div className="w-10 h-10 bg-orange-100 rounded-full flex items-center justify-center flex-shrink-0">
                <svg className="w-5 h-5 text-orange-500" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
                </svg>
              </div>
              <div className="flex-1">
                <h3 className="text-lg font-medium text-gray-900">确定删除对话？</h3>
              </div>
              <button
                onClick={() => setDeleteConfirm(null)}
                className="text-gray-400 hover:text-gray-600"
              >
                <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                </svg>
              </button>
            </div>
            <p className="mt-3 text-sm text-gray-500">删除后，聊天记录将不可恢复。</p>
            <div className="mt-6 flex gap-3 justify-end">
              <button
                onClick={() => setDeleteConfirm(null)}
                className="px-4 py-2 text-gray-700 bg-gray-100 hover:bg-gray-200 rounded-lg transition-colors"
              >
                取消
              </button>
              <button
                onClick={confirmDelete}
                className="px-4 py-2 text-white bg-red-500 hover:bg-red-600 rounded-lg transition-colors"
              >
                删除
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
