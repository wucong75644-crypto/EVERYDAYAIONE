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
}: ChatHeaderProps) {
  return (
    <header className="h-14 bg-white flex items-center justify-between px-4 flex-shrink-0">
      <div className="flex items-center space-x-3">
        {sidebarCollapsed && (
          <button
            onClick={onToggleSidebar}
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
            onChange={(e) => onEditingTitleChange(e.target.value)}
            onBlur={onTitleSubmit}
            onKeyDown={(e) => {
              if (e.key === 'Enter') onTitleSubmit();
              if (e.key === 'Escape') onTitleCancel();
            }}
            autoFocus
            aria-label="编辑对话标题"
            className="text-lg font-medium bg-gray-100 px-2 py-1 rounded outline-none focus:ring-2 focus:ring-blue-500"
          />
        ) : (
          <h1
            onDoubleClick={onTitleDoubleClick}
            className="text-lg font-medium text-gray-900 truncate max-w-md cursor-pointer hover:text-blue-600"
            title="双击编辑标题"
          >
            {conversationTitle}
          </h1>
        )}
      </div>
      <div className="flex items-center space-x-2 text-sm text-gray-600">
        <span>剩余积分:</span>
        <span className="font-medium text-blue-600">{userCredits}</span>
      </div>
    </header>
  );
});

export default ChatHeader;
