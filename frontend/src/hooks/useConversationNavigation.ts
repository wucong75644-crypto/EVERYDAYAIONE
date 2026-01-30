/**
 * 对话导航 Hook
 *
 * 职责：
 * - handleNewConversation：创建新对话
 * - handleSelectConversation：选择对话
 * - handleConversationCreated：对话创建成功回调
 * - handleConversationRename / handleConversationDelete：重命名和删除回调
 * - 顶部标题编辑状态管理
 */

import { useState, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import { updateConversation } from '../services/conversation';

/** 标题乐观更新状态 */
export interface ConversationOptimisticTitleUpdate {
  id: string;
  title: string;
}

/** 新对话乐观更新状态 */
export interface ConversationOptimisticNew {
  id: string;
  title: string;
}

/** Hook 参数接口 */
export interface UseConversationNavigationParams {
  currentConversationId: string | null;
  setCurrentConversationId: React.Dispatch<React.SetStateAction<string | null>>;
  conversationTitle: string;
  setConversationTitle: React.Dispatch<React.SetStateAction<string>>;
}

/** Hook 返回接口 */
export interface UseConversationNavigationReturn {
  /** 创建新对话 */
  handleNewConversation: () => void;
  /** 选择对话 */
  handleSelectConversation: (id: string, title: string, modelId?: string | null) => void;
  /** 对话创建成功回调 */
  handleConversationCreated: (id: string, title: string) => void;
  /** 侧边栏重命名回调 */
  handleConversationRename: (id: string, newTitle: string) => void;
  /** 删除对话回调 */
  handleConversationDelete: (id: string) => void;
  /** 标题乐观更新状态 */
  conversationOptimisticTitleUpdate: ConversationOptimisticTitleUpdate | null;
  /** 新对话乐观更新状态 */
  conversationOptimisticNew: ConversationOptimisticNew | null;
  /** 是否正在编辑标题 */
  isEditingTitle: boolean;
  /** 编辑中的标题 */
  editingTitle: string;
  /** 设置编辑中的标题 */
  setEditingTitle: React.Dispatch<React.SetStateAction<string>>;
  /** 顶部标题双击编辑 */
  handleTitleDoubleClick: () => void;
  /** 提交标题编辑 */
  handleTitleSubmit: () => Promise<void>;
  /** 取消标题编辑 */
  cancelTitleEdit: () => void;
}

/**
 * 对话导航 Hook
 *
 * 提取自 Chat.tsx 的对话导航和管理逻辑
 */
export function useConversationNavigation({
  currentConversationId,
  setCurrentConversationId,
  conversationTitle,
  setConversationTitle,
}: UseConversationNavigationParams): UseConversationNavigationReturn {
  const navigate = useNavigate();

  // 标题乐观更新（顶部标题修改时同步更新侧边栏）
  const [conversationOptimisticTitleUpdate, setConversationOptimisticTitleUpdate] =
    useState<ConversationOptimisticTitleUpdate | null>(null);

  // 新对话乐观更新（创建新对话时立即添加到列表顶部）
  const [conversationOptimisticNew, setConversationOptimisticNew] =
    useState<ConversationOptimisticNew | null>(null);

  // 顶部标题编辑状态
  const [isEditingTitle, setIsEditingTitle] = useState(false);
  const [editingTitle, setEditingTitle] = useState('');

  // 创建新对话
  const handleNewConversation = useCallback(() => {
    // 跳转到主聊天页面（会触发 URL 变化，自动清除状态）
    navigate('/chat');
  }, [navigate]);

  // 选择对话
  const handleSelectConversation = useCallback(
    (id: string, title: string, modelId?: string | null) => {
      void title;
      void modelId;
      // 路由跳转到对话详情页（URL 变化会触发 useEffect 统一处理状态更新）
      navigate(`/chat/${id}`);
    },
    [navigate]
  );

  // 对话创建成功
  const handleConversationCreated = useCallback(
    (id: string, title: string) => {
      setCurrentConversationId(id);
      setConversationTitle(title);
      // 乐观更新：直接添加新对话到列表顶部（不触发 API 调用）
      setConversationOptimisticNew({ id, title });
      // 更新 URL，确保刷新后能正确加载对话
      navigate(`/chat/${id}`, { replace: true });
    },
    [navigate, setCurrentConversationId, setConversationTitle]
  );

  // 侧边栏重命名回调：同步更新顶部标题
  const handleConversationRename = useCallback(
    (id: string, newTitle: string) => {
      if (id === currentConversationId) {
        setConversationTitle(newTitle);
      }
    },
    [currentConversationId, setConversationTitle]
  );

  // 删除对话回调：清除当前对话
  const handleConversationDelete = useCallback(
    (id: string) => {
      if (id === currentConversationId) {
        // 跳转到主聊天页面（会触发状态清除）
        navigate('/chat');
      }
    },
    [currentConversationId, navigate]
  );

  // 顶部标题双击编辑
  const handleTitleDoubleClick = useCallback(() => {
    if (currentConversationId) {
      setEditingTitle(conversationTitle);
      setIsEditingTitle(true);
    }
  }, [currentConversationId, conversationTitle]);

  // 提交标题编辑
  const handleTitleSubmit = useCallback(async () => {
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
  }, [editingTitle, conversationTitle, currentConversationId, setConversationTitle]);

  // 取消标题编辑
  const cancelTitleEdit = useCallback(() => {
    setIsEditingTitle(false);
  }, []);

  return {
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
  };
}
