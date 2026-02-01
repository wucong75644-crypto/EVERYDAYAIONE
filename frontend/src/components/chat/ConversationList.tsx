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
import { useTaskStore } from '../../stores/useTaskStore';
import {
  type OptimisticUpdate,
  type OptimisticTitleUpdate,
  type OptimisticNewConversation,
  CONVERSATIONS_CACHE_KEY,
  groupConversationsByDate,
} from './conversationUtils';
import ConversationItem from './ConversationItem';
import ContextMenu from './ContextMenu';
import DropdownMenu from './DropdownMenu';
import DeleteConfirmModal from './DeleteConfirmModal';
import { MODAL_CLOSE_ANIMATION_DURATION } from '../../constants/animations';

interface ConversationListProps {
  currentConversationId: string | null;
  onSelectConversation: (id: string, title: string, modelId?: string | null) => void;
  optimisticUpdate?: OptimisticUpdate | null;
  optimisticTitleUpdate?: OptimisticTitleUpdate | null;
  optimisticNewConversation?: OptimisticNewConversation | null;
  onRename?: (id: string, newTitle: string) => void;
  onDelete?: (id: string) => void;
  searchQuery?: string;
}

export default function ConversationList({
  currentConversationId,
  onSelectConversation,
  optimisticUpdate = null,
  optimisticTitleUpdate = null,
  optimisticNewConversation = null,
  onRename,
  onDelete,
  searchQuery = '',
}: ConversationListProps) {
  const [conversations, setConversations] = useState<ConversationListItem[]>(() => {
    try {
      const cached = localStorage.getItem(CONVERSATIONS_CACHE_KEY);
      return cached ? JSON.parse(cached) : [];
    } catch {
      return [];
    }
  });
  const [loading, setLoading] = useState(() => {
    try {
      const cached = localStorage.getItem(CONVERSATIONS_CACHE_KEY);
      return !cached;
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
  const [contextMenuClosing, setContextMenuClosing] = useState(false);
  const [dropdownMenu, setDropdownMenu] = useState<{
    id: string;
    title: string;
    x: number;
    y: number;
  } | null>(null);
  const [dropdownClosing, setDropdownClosing] = useState(false);
  const [hoveredId, setHoveredId] = useState<string | null>(null);
  const [renameId, setRenameId] = useState<string | null>(null);
  const [renameTitle, setRenameTitle] = useState('');
  const [deleteConfirm, setDeleteConfirm] = useState<{
    id: string;
    title: string;
  } | null>(null);
  const [deleteConfirmClosing, setDeleteConfirmClosing] = useState(false);

  const hasAutoSelected = useRef(false);
  const hasInitialLoaded = useRef(false);  // 防止重复初始加载
  const onSelectConversationRef = useRef(onSelectConversation);
  onSelectConversationRef.current = onSelectConversation;

  // 本地修改追踪（用于乐观合并，防止 API 响应覆盖本地修改）
  const localModificationsRef = useRef<{
    deleted: Set<string>;      // 已删除的对话 ID
    renamed: Map<string, string>;  // ID → 新标题
  }>({ deleted: new Set(), renamed: new Map() });

  // 加载对话列表（乐观合并策略：保留本地修改）
  const loadConversations = useCallback(async (isInitial = false) => {
    // 防止重复初始加载
    if (isInitial && hasInitialLoaded.current) return;
    if (isInitial) hasInitialLoaded.current = true;

    try {
      if (isInitial) {
        // 仅当没有缓存时才显示 loading
        const cached = localStorage.getItem(CONVERSATIONS_CACHE_KEY);
        if (!cached) setLoading(true);
      }
      const response = await getConversationList();

      // 乐观合并：过滤已删除的对话，应用已重命名的标题
      const { deleted, renamed } = localModificationsRef.current;
      const mergedConversations = response.conversations
        .filter((c) => !deleted.has(c.id))  // 过滤已删除
        .map((c) => renamed.has(c.id) ? { ...c, title: renamed.get(c.id)! } : c);  // 应用重命名

      setConversations(mergedConversations);
      try {
        localStorage.setItem(CONVERSATIONS_CACHE_KEY, JSON.stringify(mergedConversations));
      } catch {
        // 缓存保存失败，不影响功能
      }

      // 清理已同步的本地修改（下次加载不再需要）
      localModificationsRef.current = { deleted: new Set(), renamed: new Map() };

      if (isInitial && !hasAutoSelected.current && mergedConversations.length > 0) {
        hasAutoSelected.current = true;
        const mostRecent = mergedConversations[0];
        onSelectConversationRef.current(mostRecent.id, mostRecent.title, mostRecent.model_id);
      }
    } catch (error) {
      console.error('加载对话列表失败:', error);
    } finally {
      if (isInitial) {
        setLoading(false);
      }
    }
  }, []);  // 移除 conversations.length 依赖，避免重复创建

  useEffect(() => {
    loadConversations(true);
  }, [loadConversations]);

  // 乐观更新：消息更新时移动对话到最前
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

  // 乐观更新：标题更新
  useEffect(() => {
    if (!optimisticTitleUpdate) return;
    setConversations((prev) =>
      prev.map((c) =>
        c.id === optimisticTitleUpdate.id ? { ...c, title: optimisticTitleUpdate.title } : c
      )
    );
  }, [optimisticTitleUpdate]);

  // 乐观更新：新对话添加到顶部
  useEffect(() => {
    if (!optimisticNewConversation) return;
    setConversations((prev) => {
      if (prev.some((c) => c.id === optimisticNewConversation.id)) {
        return prev;
      }
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

  const closeContextMenu = () => {
    setContextMenuClosing(true);
    setTimeout(() => {
      setContextMenu(null);
      setContextMenuClosing(false);
    }, MODAL_CLOSE_ANIMATION_DURATION);
  };

  const closeDropdownMenu = () => {
    setDropdownClosing(true);
    setTimeout(() => {
      setDropdownMenu(null);
      setDropdownClosing(false);
    }, MODAL_CLOSE_ANIMATION_DURATION);
  };

  const closeDeleteConfirm = () => {
    setDeleteConfirmClosing(true);
    setTimeout(() => {
      setDeleteConfirm(null);
      setDeleteConfirmClosing(false);
    }, MODAL_CLOSE_ANIMATION_DURATION);
  };

  const handleContextMenu = (e: React.MouseEvent, id: string, title: string) => {
    e.preventDefault();
    setContextMenu({ id, title, x: e.clientX, y: e.clientY });
  };

  const handleShowDropdown = (e: React.MouseEvent, id: string, title: string) => {
    e.preventDefault();
    e.stopPropagation();

    // Toggle: 如果点击的是已打开的菜单，则关闭它
    if (dropdownMenu?.id === id) {
      closeDropdownMenu();
      return;
    }

    // 获取按钮位置，用于定位菜单
    const buttonRect = (e.currentTarget as HTMLElement).getBoundingClientRect();

    // 菜单高度精确计算（7个选项每项40px + 分隔线10px + padding 8px）
    const menuHeight = 298;

    // 水平位置：紧贴按钮右侧
    const x = buttonRect.right + 5;

    // 垂直位置逻辑：
    // 1. 默认：菜单顶部对齐按钮顶部（向下展开）
    // 2. 底部被遮挡：菜单底部对齐按钮底部（向上展开）
    let y = buttonRect.top;

    // 检测底部是否会被遮挡（保留20px安全边距）
    const viewportHeight = window.innerHeight;
    const spaceBelow = viewportHeight - buttonRect.bottom;
    const spaceAbove = buttonRect.top;

    if (spaceBelow < menuHeight && spaceAbove > spaceBelow) {
      // 底部空间不足且上方空间更大，向上展开
      y = buttonRect.bottom - menuHeight;
      // 确保不超出顶部
      if (y < 10) {
        y = 10;
      }
    } else {
      // 向下展开（默认）
      y = buttonRect.top;
      // 如果超出底部，调整到最大可显示位置
      if (y + menuHeight > viewportHeight - 10) {
        y = Math.max(10, viewportHeight - menuHeight - 10);
      }
    }

    setDropdownMenu({
      id,
      title,
      x,
      y
    });
  };

  const handleDelete = (id: string, title?: string) => {
    setDeleteConfirm({ id, title: title || contextMenu?.title || dropdownMenu?.title || '' });
    closeContextMenu();
    closeDropdownMenu();
  };

  const confirmDelete = async () => {
    if (!deleteConfirm) return;
    const deleteId = deleteConfirm.id;

    // 记录本地修改（防止 API 响应覆盖）
    localModificationsRef.current.deleted.add(deleteId);

    // 乐观更新 UI
    const updatedConversations = conversations.filter((c) => c.id !== deleteId);
    setConversations(updatedConversations);
    try {
      localStorage.setItem(CONVERSATIONS_CACHE_KEY, JSON.stringify(updatedConversations));
    } catch {
      // 缓存更新失败，不影响功能
    }
    onDelete?.(deleteId);
    closeDeleteConfirm();

    // 异步调用后端（失败时本地修改会在下次加载时被恢复）
    try {
      await deleteConversation(deleteId);
    } catch (error) {
      console.error('删除对话失败:', error);
      // 删除失败，清除本地修改记录，下次加载会恢复
      localModificationsRef.current.deleted.delete(deleteId);
    }
  };

  const handleStartRename = (id: string, title: string) => {
    setRenameId(id);
    setRenameTitle(title);
    closeContextMenu();
    closeDropdownMenu();
  };

  // Placeholder handlers for new menu options
  const handlePin = (id: string) => {
    void id;
    closeDropdownMenu();
    // TODO: Implement pin functionality
  };

  const handleShare = (id: string) => {
    void id;
    closeDropdownMenu();
    // TODO: Implement share functionality
  };

  const handleBatchManage = () => {
    closeDropdownMenu();
    // TODO: Implement batch manage functionality
  };

  const handleMoveToGroup = (id: string) => {
    void id;
    closeDropdownMenu();
    // TODO: Implement move to group functionality
  };

  const handleExport = (id: string) => {
    void id;
    closeDropdownMenu();
    // TODO: Implement export functionality
  };

  const handleSubmitRename = async () => {
    if (!renameId || !renameTitle.trim()) {
      setRenameId(null);
      return;
    }
    const newTitle = renameTitle.trim();
    const oldId = renameId;

    // 记录本地修改（防止 API 响应覆盖）
    localModificationsRef.current.renamed.set(oldId, newTitle);

    // 乐观更新 UI
    const updatedConversations = conversations.map((c) =>
      c.id === oldId ? { ...c, title: newTitle } : c
    );
    setConversations(updatedConversations);
    try {
      localStorage.setItem(CONVERSATIONS_CACHE_KEY, JSON.stringify(updatedConversations));
    } catch {
      // 缓存更新失败，不影响功能
    }
    onRename?.(oldId, newTitle);
    setRenameId(null);

    // 异步调用后端（失败时本地修改会在下次加载时被恢复）
    try {
      await updateConversation(oldId, { title: newTitle });
    } catch (error) {
      console.error('重命名失败:', error);
      // 重命名失败，清除本地修改记录，下次加载会恢复原标题
      localModificationsRef.current.renamed.delete(oldId);
    }
  };

  useEffect(() => {
    const handleClick = () => {
      closeContextMenu();
      closeDropdownMenu();
    };
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
          <div className="px-4 py-1.5 text-xs text-gray-400 font-normal uppercase tracking-wide">
            {group}
          </div>
          {convs.map((conv) => (
            <ConversationItem
              key={conv.id}
              conv={conv}
              currentConversationId={currentConversationId}
              isRenaming={renameId === conv.id}
              renameTitle={renameTitle}
              isHovered={hoveredId === conv.id}
              isDropdownOpen={dropdownMenu?.id === conv.id}
              onSelect={() => {
                onSelectConversation(conv.id, conv.title, conv.model_id);
                useTaskStore.getState().clearRecentlyCompleted(conv.id);
              }}
              onStartRename={() => handleStartRename(conv.id, conv.title)}
              onContextMenu={(e) => handleContextMenu(e, conv.id, conv.title)}
              onShowDropdown={(e) => handleShowDropdown(e, conv.id, conv.title)}
              onHoverChange={(hovered) => setHoveredId(hovered ? conv.id : null)}
              onRenameChange={setRenameTitle}
              onRenameSubmit={handleSubmitRename}
              onRenameCancel={() => setRenameId(null)}
            />
          ))}
        </div>
      ))}

      {contextMenu && (
        <ContextMenu
          x={contextMenu.x}
          y={contextMenu.y}
          closing={contextMenuClosing}
          onRename={() => handleStartRename(contextMenu.id, contextMenu.title)}
          onDelete={() => handleDelete(contextMenu.id)}
        />
      )}

      {dropdownMenu && (
        <DropdownMenu
          x={dropdownMenu.x}
          y={dropdownMenu.y}
          closing={dropdownClosing}
          onRename={() => handleStartRename(dropdownMenu.id, dropdownMenu.title)}
          onPin={() => handlePin(dropdownMenu.id)}
          onShare={() => handleShare(dropdownMenu.id)}
          onBatchManage={handleBatchManage}
          onMoveToGroup={() => handleMoveToGroup(dropdownMenu.id)}
          onExport={() => handleExport(dropdownMenu.id)}
          onDelete={() => handleDelete(dropdownMenu.id, dropdownMenu.title)}
        />
      )}

      {deleteConfirm && (
        <DeleteConfirmModal
          closing={deleteConfirmClosing}
          onConfirm={confirmDelete}
          onCancel={closeDeleteConfirm}
        />
      )}
    </div>
  );
}
