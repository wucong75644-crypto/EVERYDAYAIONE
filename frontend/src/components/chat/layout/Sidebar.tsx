/**
 * 聊天页面左侧栏
 *
 * 包含：
 * - 新建对话按钮
 * - 对话列表
 * - 用户头像和导航
 */

import { useState, useRef } from 'react';
import { Link } from 'react-router-dom';
import { useAuthStore } from '../../../stores/useAuthStore';
import { useMessageStore } from '../../../stores/useMessageStore';
import { useClickOutside } from '../../../hooks/useClickOutside';
import { useLogout } from '../../../hooks/useLogout';
import { Brain, Settings2, Search, ChevronsLeft, Plus, X, Settings, LogOut } from 'lucide-react';
import ConversationList from './ConversationList';
import SettingsModal from '../modals/SettingsModal';
import MemoryModal from '../modals/MemoryModal';
import AdminPanel from '../../admin/AdminPanel';
import { useMemoryStore } from '../../../stores/useMemoryStore';

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
  optimisticUpdate = null,
  optimisticTitleUpdate = null,
  optimisticNewConversation = null,
  onRename,
  onDelete,
}: SidebarProps) {
  const { user, currentOrg } = useAuthStore();
  const logout = useLogout();
  const [showAdminPanel, setShowAdminPanel] = useState(false);
  const showAdminEntry =
    user?.role === 'super_admin' ||
    (currentOrg && ['owner', 'admin'].includes(currentOrg.role));
  const [showUserMenu, setShowUserMenu] = useState(false);
  const [showSearch, setShowSearch] = useState(false);
  const [showSettingsModal, setShowSettingsModal] = useState(false);
  const [searchQuery, setSearchQuery] = useState('');
  const searchInputRef = useRef<HTMLInputElement>(null);
  const userMenuRef = useRef<HTMLDivElement>(null);

  const handleLogout = () => {
    setShowUserMenu(false);
    logout();
  };

  // 点击外部关闭搜索框（仅在搜索框为空时）
  useClickOutside(
    searchInputRef,
    showSearch,
    () => setShowSearch(false),
    !!searchQuery // 有搜索内容时跳过关闭
  );

  // 点击外部关闭用户菜单
  useClickOutside(userMenuRef, showUserMenu, () => setShowUserMenu(false));

  // 包装 onSelectConversation，选择对话时关闭搜索并清除完成提醒
  const handleSelectConversation = (id: string, title: string, modelId?: string | null) => {
    setShowSearch(false);
    setSearchQuery('');
    useMessageStore.getState().clearRecentlyCompleted(id);
    onSelectConversation(id, title, modelId);
  };

  if (collapsed) {
    return null;
  }

  return (
    <aside className="w-64 bg-surface-card text-text-primary flex flex-col flex-shrink-0 border-r border-border-default">
      {/* 顶部区域 */}
      <div className="p-3">
        <div className="flex items-center justify-between mb-3">
          <span className="text-lg font-semibold">每日AI</span>
          <div className="flex items-center space-x-1">
            {/* 搜索按钮 */}
            <button
              onClick={() => setShowSearch(!showSearch)}
              className="p-1.5 hover:bg-hover rounded-lg transition-base"
              title="搜索对话"
            >
              <Search className="w-5 h-5" />
            </button>
            {/* 收起按钮 */}
            <button
              onClick={onToggle}
              className="p-1.5 hover:bg-hover rounded-lg transition-base"
              title="收起侧边栏"
            >
              <ChevronsLeft className="w-5 h-5" />
            </button>
          </div>
        </div>

        {/* 新建对话按钮 */}
        <div className="relative">
          <button
            onClick={onNewConversation}
            className="w-full flex items-center justify-center space-x-2 px-3 py-2 bg-accent hover:bg-accent-hover text-text-on-accent rounded-lg transition-base"
          >
            <Plus className="w-4 h-4" />
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
                className="w-full h-full px-3 pr-10 py-2 bg-surface-card text-text-primary border border-accent rounded-lg text-sm outline-none shadow-lg"
              />
              {/* 关闭按钮 */}
              <button
                onClick={() => {
                  setShowSearch(false);
                  setSearchQuery('');
                }}
                className="absolute right-2 p-1 hover:bg-hover rounded-lg transition-base"
                title="关闭搜索"
              >
                <X className="w-4 h-4 text-text-tertiary" />
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
          optimisticUpdate={optimisticUpdate}
          optimisticTitleUpdate={optimisticTitleUpdate}
          optimisticNewConversation={optimisticNewConversation}
          onRename={onRename}
          onDelete={onDelete}
          searchQuery={searchQuery}
        />
      </div>

      {/* 记忆入口 */}
      <div className="px-3 pb-1">
        <button
          onClick={useMemoryStore.getState().openModal}
          className="w-full flex items-center gap-2 px-3 py-2 text-sm text-text-secondary hover:bg-hover rounded-lg transition-base"
        >
          <Brain className="w-4 h-4" />
          <span>AI 记忆</span>
        </button>
      </div>

      {/* 管理后台入口（仅 super_admin / owner / admin 可见） */}
      {showAdminEntry && (
        <div className="px-3 pb-1">
          <button
            onClick={() => setShowAdminPanel(true)}
            className="w-full flex items-center gap-2 px-3 py-2 text-sm text-text-secondary hover:bg-hover rounded-lg transition-base"
          >
            <Settings2 className="w-4 h-4" />
            <span>管理后台</span>
          </button>
        </div>
      )}

      {/* 底部用户区域 */}
      <div className="p-3">
        <div className="flex items-center justify-between">
          {/* 用户头像和菜单 */}
          <div ref={userMenuRef} className="relative">
            <button
              onClick={() => setShowUserMenu(!showUserMenu)}
              className="flex items-center space-x-2 hover:bg-hover rounded-lg p-2 transition-base"
            >
              <div className="w-8 h-8 bg-accent rounded-full flex items-center justify-center text-sm font-medium text-text-on-accent">
                {user?.nickname?.charAt(0) || 'U'}
              </div>
            </button>

            {/* 用户下拉菜单 */}
            {showUserMenu && (
              <div className="absolute bottom-full left-0 mb-2 w-48 bg-surface-card rounded-lg shadow-lg border border-border-default py-1 text-text-secondary">
                <button
                  onClick={() => {
                    setShowUserMenu(false);
                    setShowSettingsModal(true);
                  }}
                  className="w-full px-4 py-2 text-left text-sm hover:bg-hover flex items-center space-x-2 transition-base"
                >
                  <Settings className="w-4 h-4" />
                  <span>个人设置</span>
                </button>
                <button
                  onClick={handleLogout}
                  className="w-full px-4 py-2 text-left text-sm hover:bg-error-light flex items-center space-x-2 text-error transition-base"
                >
                  <LogOut className="w-4 h-4" />
                  <span>退出登录</span>
                </button>
              </div>
            )}
          </div>

          {/* 模型广场按钮 */}
          <Link
            to="/"
            className="px-3 py-1.5 text-sm text-text-secondary bg-hover hover:bg-active rounded-lg transition-base"
          >
            模型广场
          </Link>
        </div>
      </div>

      {/* 个人设置弹框 */}
      <SettingsModal
        isOpen={showSettingsModal}
        onClose={() => setShowSettingsModal(false)}
      />

      {/* 记忆管理弹框 */}
      <MemoryModal />

      {/* 管理后台弹框 */}
      {showAdminPanel && (
        <AdminPanel onClose={() => setShowAdminPanel(false)} />
      )}
    </aside>
  );
}
