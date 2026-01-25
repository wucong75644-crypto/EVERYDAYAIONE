/**
 * 聊天页面左侧栏
 *
 * 包含：
 * - 新建对话按钮
 * - 对话列表
 * - 用户头像和导航
 */

import { useState, useRef, useEffect } from 'react';
import { Link } from 'react-router-dom';
import { useAuthStore } from '../../stores/useAuthStore';
import ConversationList from './ConversationList';

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

interface SidebarProps {
  collapsed: boolean;
  onToggle: () => void;
  currentConversationId: string | null;
  onNewConversation: () => void;
  onSelectConversation: (id: string, title: string, modelId?: string | null) => void;
  userCredits: number;
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
}

export default function Sidebar({
  collapsed,
  onToggle,
  currentConversationId,
  onNewConversation,
  onSelectConversation,
  refreshTrigger = 0,
  optimisticUpdate = null,
  optimisticTitleUpdate = null,
  optimisticNewConversation = null,
  onRename,
  onDelete,
}: SidebarProps) {
  const { user, clearAuth } = useAuthStore();
  const [showUserMenu, setShowUserMenu] = useState(false);
  const [showSearch, setShowSearch] = useState(false);
  const [searchQuery, setSearchQuery] = useState('');
  const searchInputRef = useRef<HTMLInputElement>(null);
  const userMenuRef = useRef<HTMLDivElement>(null);

  const handleLogout = () => {
    clearAuth();
    setShowUserMenu(false);
  };

  // 点击外部关闭搜索框（仅在搜索框为空时）
  useEffect(() => {
    if (!showSearch || searchQuery) return; // 有搜索内容时不自动关闭

    const handleClickOutside = (e: MouseEvent) => {
      if (searchInputRef.current && !searchInputRef.current.contains(e.target as Node)) {
        setShowSearch(false);
      }
    };

    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, [showSearch, searchQuery]);

  // 点击外部关闭用户菜单
  useEffect(() => {
    if (!showUserMenu) return;

    const handleClickOutside = (e: MouseEvent) => {
      if (userMenuRef.current && !userMenuRef.current.contains(e.target as Node)) {
        setShowUserMenu(false);
      }
    };

    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, [showUserMenu]);

  // 包装 onSelectConversation，选择对话时关闭搜索
  const handleSelectConversation = (id: string, title: string, modelId?: string | null) => {
    setShowSearch(false);
    setSearchQuery('');
    onSelectConversation(id, title, modelId);
  };

  if (collapsed) {
    return null;
  }

  return (
    <aside className="w-64 bg-white text-gray-900 flex flex-col flex-shrink-0 border-r border-gray-200">
      {/* 顶部区域 */}
      <div className="p-3">
        <div className="flex items-center justify-between mb-3">
          <span className="text-lg font-semibold">每日AI</span>
          <div className="flex items-center space-x-1">
            {/* 搜索按钮 */}
            <button
              onClick={() => setShowSearch(!showSearch)}
              className="p-1.5 hover:bg-gray-100 rounded-lg transition-colors"
              title="搜索对话"
            >
              <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
              </svg>
            </button>
            {/* 收起按钮 */}
            <button
              onClick={onToggle}
              className="p-1.5 hover:bg-gray-100 rounded-lg transition-colors"
              title="收起侧边栏"
            >
              <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M11 19l-7-7 7-7m8 14l-7-7 7-7" />
              </svg>
            </button>
          </div>
        </div>

        {/* 新建对话按钮 */}
        <div className="relative">
          <button
            onClick={onNewConversation}
            className="w-full flex items-center justify-center space-x-2 px-3 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded-lg transition-colors"
          >
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
            </svg>
            <span className="text-sm">新对话</span>
          </button>

          {/* 搜索输入框 - 绝对定位覆盖 */}
          {showSearch && (
            <div ref={searchInputRef} className="absolute inset-0 z-10 flex items-center">
              <input
                type="text"
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                placeholder="搜索对话..."
                autoFocus
                className="w-full h-full px-3 pr-10 py-2 bg-white border border-blue-500 rounded-lg text-sm outline-none shadow-lg"
              />
              {/* 关闭按钮 */}
              <button
                onClick={() => {
                  setShowSearch(false);
                  setSearchQuery('');
                }}
                className="absolute right-2 p-1 hover:bg-gray-100 rounded-lg transition-colors"
                title="关闭搜索"
              >
                <svg className="w-4 h-4 text-gray-500" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                </svg>
              </button>
            </div>
          )}
        </div>
      </div>

      {/* 对话列表 */}
      <div className="flex-1 overflow-y-auto">
        <ConversationList
          currentConversationId={currentConversationId}
          onSelectConversation={handleSelectConversation}
          refreshTrigger={refreshTrigger}
          optimisticUpdate={optimisticUpdate}
          optimisticTitleUpdate={optimisticTitleUpdate}
          optimisticNewConversation={optimisticNewConversation}
          onRename={onRename}
          onDelete={onDelete}
          searchQuery={searchQuery}
        />
      </div>

      {/* 底部用户区域 */}
      <div className="p-3">
        <div className="flex items-center justify-between">
          {/* 用户头像和菜单 */}
          <div ref={userMenuRef} className="relative">
            <button
              onClick={() => setShowUserMenu(!showUserMenu)}
              className="flex items-center space-x-2 hover:bg-gray-100 rounded-lg p-2 transition-colors"
            >
              <div className="w-8 h-8 bg-blue-500 rounded-full flex items-center justify-center text-sm font-medium text-white">
                {user?.nickname?.charAt(0) || 'U'}
              </div>
            </button>

            {/* 用户下拉菜单 */}
            {showUserMenu && (
              <div className="absolute bottom-full left-0 mb-2 w-48 bg-white rounded-lg shadow-lg border border-gray-200 py-1 text-gray-700">
                <button
                  onClick={() => setShowUserMenu(false)}
                  className="w-full px-4 py-2 text-left text-sm hover:bg-gray-100 flex items-center space-x-2"
                >
                  <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z" />
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
                  </svg>
                  <span>个人设置</span>
                </button>
                <button
                  onClick={handleLogout}
                  className="w-full px-4 py-2 text-left text-sm hover:bg-gray-100 flex items-center space-x-2 text-red-600"
                >
                  <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M17 16l4-4m0 0l-4-4m4 4H7m6 4v1a3 3 0 01-3 3H6a3 3 0 01-3-3V7a3 3 0 013-3h4a3 3 0 013 3v1" />
                  </svg>
                  <span>退出登录</span>
                </button>
              </div>
            )}
          </div>

          {/* 模型广场按钮 */}
          <Link
            to="/models"
            className="px-3 py-1.5 text-sm text-gray-700 bg-gray-100 hover:bg-gray-200 rounded-lg transition-colors"
          >
            模型广场
          </Link>
        </div>
      </div>
    </aside>
  );
}
