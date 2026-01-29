/**
 * 单个对话项组件
 *
 * 包含对话内容显示、任务状态徽章、点击/双击/右键交互
 */

import type { ConversationListItem } from '../../services/conversation';
import { useTaskStore } from '../../stores/useTaskStore';
import { MoreHorizontal } from 'lucide-react';

interface ConversationItemContentProps {
  conv: ConversationListItem;
  currentConversationId: string | null;
  isHovered: boolean;
  isDropdownOpen: boolean;
  onShowDropdown: (e: React.MouseEvent) => void;
}

/**
 * 对话项内容（含任务状态徽章）
 */
export function ConversationItemContent({
  conv,
  currentConversationId,
  isHovered,
  isDropdownOpen,
  onShowDropdown,
}: ConversationItemContentProps) {
  const { hasActiveTask, getTask, isRecentlyCompleted } = useTaskStore();
  const isActive = hasActiveTask(conv.id);
  const task = getTask(conv.id);
  const justCompleted = isRecentlyCompleted(conv.id);

  return (
    <>
      <div className="flex items-center gap-2 relative pr-8">
        <div
          className={`text-sm truncate flex-1 ${
            currentConversationId === conv.id
              ? 'text-gray-900 font-normal'
              : 'text-gray-800 font-normal'
          }`}
        >
          {conv.title}
        </div>
        {/* 任务状态徽章 */}
        {isActive && (
          <div className="flex-shrink-0" title={task?.status === 'streaming' ? '正在生成...' : '等待中...'}>
            {task?.status === 'streaming' ? (
              <div className="flex space-x-0.5">
                <span className="w-1.5 h-1.5 bg-blue-500 rounded-full animate-bounce" style={{ animationDelay: '0ms' }}></span>
                <span className="w-1.5 h-1.5 bg-blue-500 rounded-full animate-bounce" style={{ animationDelay: '150ms' }}></span>
                <span className="w-1.5 h-1.5 bg-blue-500 rounded-full animate-bounce" style={{ animationDelay: '300ms' }}></span>
              </div>
            ) : (
              <div className="w-2 h-2 bg-yellow-400 rounded-full animate-pulse"></div>
            )}
          </div>
        )}
        {/* 完成闪烁徽章（绿色闪烁2秒） */}
        {justCompleted && !isActive && (
          <div className="flex-shrink-0" title="生成完成">
            <div className="w-2 h-2 bg-green-500 rounded-full animate-ping"></div>
          </div>
        )}
        {/* More button (绝对定位在右侧，hover或菜单打开时显示) */}
        <button
          type="button"
          onClick={onShowDropdown}
          className={`absolute right-0 top-1/2 -translate-y-1/2 p-1 rounded transition-all ${
            isDropdownOpen
              ? 'opacity-100 bg-gray-200'
              : isHovered
              ? 'opacity-100 hover:bg-gray-200'
              : 'opacity-0 pointer-events-none'
          }`}
          title="更多选项"
        >
          <MoreHorizontal className="w-4 h-4 text-gray-600" />
        </button>
      </div>
    </>
  );
}

interface ConversationItemProps {
  conv: ConversationListItem;
  currentConversationId: string | null;
  isRenaming: boolean;
  renameTitle: string;
  onSelect: () => void;
  onStartRename: () => void;
  onContextMenu: (e: React.MouseEvent) => void;
  onShowDropdown: (e: React.MouseEvent) => void;
  onRenameChange: (value: string) => void;
  onRenameSubmit: () => void;
  onRenameCancel: () => void;
  isHovered: boolean;
  isDropdownOpen: boolean;
  onHoverChange: (hovered: boolean) => void;
}

/**
 * 完整的对话项组件（包含点击、双击、右键交互）
 */
export default function ConversationItem({
  conv,
  currentConversationId,
  isRenaming,
  renameTitle,
  onSelect,
  onStartRename,
  onContextMenu,
  onShowDropdown,
  onRenameChange,
  onRenameSubmit,
  onRenameCancel,
  isHovered,
  isDropdownOpen,
  onHoverChange,
}: ConversationItemProps) {
  return (
    <div
      onClick={() => {
        if (!isRenaming) {
          onSelect();
        }
      }}
      onDoubleClick={onStartRename}
      onContextMenu={onContextMenu}
      onMouseEnter={() => onHoverChange(true)}
      onMouseLeave={() => onHoverChange(false)}
      className={`mx-2 mb-1 px-3 py-1.5 rounded-lg cursor-pointer transition-colors duration-150 outline-none ${
        currentConversationId === conv.id
          ? 'bg-blue-50'
          : 'hover:bg-gray-50'
      }`}
    >
      {isRenaming ? (
        <input
          type="text"
          value={renameTitle}
          onChange={(e) => onRenameChange(e.target.value)}
          onBlur={onRenameSubmit}
          onKeyDown={(e) => {
            if (e.key === 'Enter') {
              onRenameSubmit();
            } else if (e.key === 'Escape') {
              onRenameCancel();
            }
          }}
          autoFocus
          className="w-full bg-white text-gray-900 text-sm px-2 py-1 rounded outline-none border border-blue-500 focus:ring-2 focus:ring-blue-300"
          onClick={(e) => e.stopPropagation()}
        />
      ) : (
        <ConversationItemContent
          conv={conv}
          currentConversationId={currentConversationId}
          isHovered={isHovered}
          isDropdownOpen={isDropdownOpen}
          onShowDropdown={onShowDropdown}
        />
      )}
    </div>
  );
}
