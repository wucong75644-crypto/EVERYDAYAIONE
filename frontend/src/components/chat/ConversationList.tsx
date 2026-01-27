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
import DeleteConfirmModal from './DeleteConfirmModal';

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
  const [clickedId, setClickedId] = useState<string | null>(null);
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
  const [renameId, setRenameId] = useState<string | null>(null);
  const [renameTitle, setRenameTitle] = useState('');
  const [deleteConfirm, setDeleteConfirm] = useState<{
    id: string;
    title: string;
  } | null>(null);

  const hasAutoSelected = useRef(false);
  const onSelectConversationRef = useRef(onSelectConversation);
  onSelectConversationRef.current = onSelectConversation;

  // 加载对话列表
  const loadConversations = useCallback(async (isInitial = false) => {
    try {
      if (isInitial && conversations.length === 0) {
        setLoading(true);
      }
      const response = await getConversationList();
      setConversations(response.conversations);
      try {
        localStorage.setItem(CONVERSATIONS_CACHE_KEY, JSON.stringify(response.conversations));
      } catch (error) {
        console.warn('保存对话列表缓存失败:', error);
      }
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

  const closeContextMenu = () => setContextMenu(null);

  const handleContextMenu = (e: React.MouseEvent, id: string, title: string) => {
    e.preventDefault();
    setContextMenu({ id, title, x: e.clientX, y: e.clientY });
  };

  const handleDelete = (id: string) => {
    setDeleteConfirm({ id, title: contextMenu?.title || '' });
    closeContextMenu();
  };

  const confirmDelete = async () => {
    if (!deleteConfirm) return;
    try {
      await deleteConversation(deleteConfirm.id);
      const updatedConversations = conversations.filter((c) => c.id !== deleteConfirm.id);
      setConversations(updatedConversations);
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

  const handleStartRename = (id: string, title: string) => {
    setRenameId(id);
    setRenameTitle(title);
    closeContextMenu();
  };

  const handleSubmitRename = async () => {
    if (!renameId || !renameTitle.trim()) {
      setRenameId(null);
      return;
    }
    const newTitle = renameTitle.trim();
    const oldId = renameId;
    const updatedConversations = conversations.map((c) =>
      c.id === oldId ? { ...c, title: newTitle } : c
    );
    setConversations(updatedConversations);
    try {
      localStorage.setItem(CONVERSATIONS_CACHE_KEY, JSON.stringify(updatedConversations));
    } catch (error) {
      console.warn('更新缓存失败:', error);
    }
    onRename?.(oldId, newTitle);
    setRenameId(null);
    try {
      await updateConversation(oldId, { title: newTitle });
    } catch (error) {
      console.error('重命名失败:', error);
    }
  };

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
              isClicked={clickedId === conv.id}
              isRenaming={renameId === conv.id}
              renameTitle={renameTitle}
              onSelect={() => {
                setClickedId(conv.id);
                onSelectConversation(conv.id, conv.title, conv.model_id);
                useTaskStore.getState().clearRecentlyCompleted(conv.id);
                setTimeout(() => setClickedId(null), 300);
              }}
              onStartRename={() => handleStartRename(conv.id, conv.title)}
              onContextMenu={(e) => handleContextMenu(e, conv.id, conv.title)}
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
          onRename={() => handleStartRename(contextMenu.id, contextMenu.title)}
          onDelete={() => handleDelete(contextMenu.id)}
        />
      )}

      {deleteConfirm && (
        <DeleteConfirmModal
          onConfirm={confirmDelete}
          onCancel={() => setDeleteConfirm(null)}
        />
      )}
    </div>
  );
}
