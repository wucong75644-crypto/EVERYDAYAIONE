/**
 * 聊天页面顶部导航栏组件
 *
 * 职责：
 * - 渲染顶部导航栏
 * - 标题显示和双击编辑
 * - 侧边栏展开按钮
 * - 积分显示
 */

import { memo } from 'react';
import { Brain, Clock, FolderOpen, PanelLeftOpen, Search, Users } from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import { useMemoryStore } from '../../../stores/useMemoryStore';
import { usePermission } from '../../../hooks/usePermission';

/** 组件属性接口 */
export interface ChatHeaderProps {
  /** 侧边栏是否收起 */
  sidebarCollapsed: boolean;
  /** 切换侧边栏 */
  onToggleSidebar: () => void;
  /** 对话标题 */
  conversationTitle: string;
  /** 是否正在编辑标题 */
  isEditingTitle: boolean;
  /** 编辑中的标题 */
  editingTitle: string;
  /** 设置编辑中的标题 */
  onEditingTitleChange: (value: string) => void;
  /** 顶部标题双击编辑 */
  onTitleDoubleClick: () => void;
  /** 提交标题编辑 */
  onTitleSubmit: () => void;
  /** 取消标题编辑 */
  onTitleCancel: () => void;
  /** 用户积分 */
  userCredits: number;
  /** 打开消息搜索面板（V3 Phase 4，可选） */
  onOpenSearch?: () => void;
  /** 打开定时任务面板（可选，仅企业用户可见） */
  onOpenScheduledTasks?: () => void;
  /** 打开工作区文件浏览器 */
  onOpenWorkspace?: () => void;
}

/**
 * 聊天页面顶部导航栏
 *
 * 显示对话标题（支持双击编辑）和用户积分
 */
export const ChatHeader = memo(function ChatHeader({
  sidebarCollapsed,
  onToggleSidebar,
  conversationTitle,
  isEditingTitle,
  editingTitle,
  onEditingTitleChange,
  onTitleDoubleClick,
  onTitleSubmit,
  onTitleCancel,
  userCredits,
  onOpenSearch,
  onOpenScheduledTasks,
  onOpenWorkspace,
}: ChatHeaderProps) {
  const navigate = useNavigate();
  // 仅管理员（boss/vp/manager/deputy 中的人事主管）能进组织管理
  // 用 sys.member.edit 权限点判定（boss/vp/hr-manager 持有）
  const canManageMembers = usePermission('sys.member.edit');

  return (
    <header className="h-14 bg-surface-card flex items-center justify-between px-4 flex-shrink-0">
      <div className="flex items-center space-x-3">
        {sidebarCollapsed && (
          <button
            onClick={onToggleSidebar}
            className="p-2 hover:bg-hover rounded-lg transition-base"
            title="展开侧边栏"
          >
            <PanelLeftOpen className="w-5 h-5 text-text-secondary" />
          </button>
        )}
        {isEditingTitle ? (
          <input
            type="text"
            value={editingTitle}
            onChange={(e) => onEditingTitleChange(e.target.value)}
            onBlur={onTitleSubmit}
            onKeyDown={(e) => {
              if (e.key === 'Enter') onTitleSubmit();
              if (e.key === 'Escape') onTitleCancel();
            }}
            autoFocus
            aria-label="编辑对话标题"
            className="text-lg font-medium bg-hover text-text-primary px-2 py-1 rounded outline-none focus:ring-2 focus:ring-focus-ring"
          />
        ) : (
          <h1
            onDoubleClick={onTitleDoubleClick}
            className="text-lg font-medium text-text-primary truncate max-w-md cursor-pointer hover:text-accent transition-base"
            title="双击编辑标题"
          >
            {conversationTitle}
          </h1>
        )}
      </div>
      <div className="flex items-center space-x-3 text-sm text-text-tertiary">
        {onOpenWorkspace && (
          <button
            onClick={onOpenWorkspace}
            className="p-1.5 hover:bg-hover rounded-lg transition-base"
            title="工作区"
            aria-label="打开工作区"
          >
            <FolderOpen className="w-5 h-5 text-text-tertiary" />
          </button>
        )}
        {onOpenSearch && (
          <button
            onClick={onOpenSearch}
            className="p-1.5 hover:bg-hover rounded-lg transition-base"
            title="搜索消息 (Cmd+F)"
            aria-label="搜索消息"
          >
            <Search className="w-5 h-5 text-text-tertiary" />
          </button>
        )}
        {onOpenScheduledTasks && (
          <button
            onClick={onOpenScheduledTasks}
            className="p-1.5 hover:bg-hover rounded-lg transition-base"
            title="定时任务"
            aria-label="定时任务"
          >
            <Clock className="w-5 h-5 text-text-tertiary" />
          </button>
        )}
        {canManageMembers && (
          <button
            onClick={() => navigate('/settings/organization')}
            className="p-1.5 hover:bg-hover rounded-lg transition-base"
            title="组织管理"
            aria-label="组织管理"
          >
            <Users className="w-5 h-5 text-text-tertiary" />
          </button>
        )}
        <button
          onClick={useMemoryStore.getState().openModal}
          className="p-1.5 hover:bg-hover rounded-lg transition-base"
          title="AI 记忆"
          aria-label="打开记忆管理"
        >
          <Brain className="w-5 h-5 text-text-tertiary" />
        </button>
        <div className="flex items-center space-x-2">
          <span>剩余积分:</span>
          <span className="font-medium text-accent">{userCredits}</span>
        </div>
      </div>
    </header>
  );
});

export default ChatHeader;
