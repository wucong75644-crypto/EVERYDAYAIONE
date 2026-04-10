/**
 * 单个对话项组件
 *
 * 包含对话内容显示、任务状态徽章、点击/双击/右键交互
 */

import { memo } from 'react';
import type { ConversationListItem } from '../../../services/conversation';
import { useMessageStore } from '../../../stores/useMessageStore';
import { MoreHorizontal } from 'lucide-react';
import { cn } from '../../../utils/cn';

function WecomIcon({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
      <path
        fill="#2B7EF7"
        d="M12 1c6.075 0 11 4.925 11 11s-4.925 11-11 11S1 18.075 1 12S5.925 1 12 1m3.52 15.49a.35.35 0 0 0-.24.1c-.14.13-.16.34.02.53l.07.07c.44.44.74.99.85 1.57c0 .02.04.23.04.23c.05.19.15.37.29.5c.21.21.51.34.82.34c.3 0 .59-.12.8-.33c.44-.44.44-1.16 0-1.61c-.15-.15-.34-.26-.53-.3l-.15-.03c-.61-.11-1.17-.41-1.62-.86c-.03-.03-.07-.07-.1-.11c-.06-.074-.16-.1-.25-.1M11 4.75c-2.117 0-4.264.77-5.75 2.31C4.111 8.246 3.5 9.72 3.5 11.24c0 1.06.3 2.12.88 3.06c.47.695.993 1.371 1.66 1.89l-.384 1.624a.6.6 0 0 0 .856.673L8.64 17.41c.53.166 1.08.234 1.63.3a8.3 8.3 0 0 0 1.7-.03l.38-.05q.283-.046.564-.112a2.33 2.33 0 0 1-.92-1.605l-.254.037c-.62.067-1.232.03-1.85-.04c-.43-.057-.838-.185-1.25-.31l-1.02.5l.23-.67l-.74-.6c-.513-.401-.917-.934-1.28-1.47c-.4-.65-.61-1.38-.61-2.11c0-1.08.456-2.119 1.26-2.97c1.158-1.198 2.854-1.78 4.5-1.78c1.54 0 3.108.513 4.24 1.58c.365.365.707.75.95 1.21c.177.354.338.722.424 1.107a2.34 2.34 0 0 1 1.811.123c-.075-.716-.33-1.4-.665-2.04c-.329-.62-.776-1.155-1.27-1.65c-1.468-1.38-3.471-2.08-5.47-2.08m9.37 9.77a1.136 1.136 0 0 0-1.1.86l-.03.15a3.1 3.1 0 0 1-.86 1.63c-.04.03-.07.07-.11.1c-.14.13-.14.35 0 .49c.07.06.17.1.26.1h.01c.07 0 .15-.02.26-.13l.07-.07c.44-.44.99-.74 1.57-.85c.023 0 .227-.04.23-.04c.2-.06.37-.16.5-.3c.44-.44.44-1.17 0-1.61c-.21-.21-.5-.33-.8-.33m-4.21-1.07c-.08 0-.16.03-.27.14l-.07.07c-.44.44-.99.74-1.57.85c-.02 0-.23.04-.23.04c-.2.06-.37.16-.5.3c-.44.44-.44 1.17 0 1.61c.21.21.51.34.82.34c.3 0 .59-.12.8-.33c.15-.16.25-.34.29-.53a.4.4 0 0 0 .03-.16c.11-.61.41-1.18.86-1.63c.03-.03.06-.06.1-.09c.146-.115.13-.36 0-.49a.34.34 0 0 0-.26-.12m1.18-1.97c-.3 0-.59.12-.8.33c-.44.44-.44 1.16 0 1.61c.15.15.34.26.53.3c.054.006.144.029.15.03c.61.12 1.17.41 1.62.86c.03.03.07.07.1.11c.08.08.16.1.25.1c.1 0 .16-.04.23-.11c.12-.13.14-.32-.02-.52l-.08-.08c-.44-.44-.74-.99-.85-1.57c0-.02-.04-.23-.04-.23c-.05-.19-.15-.37-.29-.5c-.21-.21-.5-.33-.8-.33"
      />
    </svg>
  );
}

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
  const hasUnread = useMessageStore((state) => state.recentlyCompleted.has(conv.id));

  return (
    <>
      <div className="flex items-center gap-2 relative pr-8">
        {(conv.source === 'wecom' || conv.title.startsWith('企微')) && (
          <WecomIcon className="w-4 h-4 flex-shrink-0" />
        )}
        <div
          className={cn(
            'text-sm truncate flex-1 font-normal',
            currentConversationId === conv.id ? 'text-text-primary' : 'text-text-secondary',
          )}
        >
          {conv.title}
        </div>
        {/* 完成通知（仅非当前对话，持续闪烁直到用户点击查看） */}
        {hasUnread && conv.id !== currentConversationId && (
          <div className="flex-shrink-0" title="生成完成，点击查看">
            <div className="w-2 h-2 bg-success rounded-full animate-dot-pulse"></div>
          </div>
        )}
        {/* More button (绝对定位在右侧，hover或菜单打开时显示) */}
        <button
          type="button"
          onClick={onShowDropdown}
          className={cn(
            'absolute right-0 top-1/2 -translate-y-1/2 p-1 rounded transition-all',
            isDropdownOpen && 'opacity-100 bg-active',
            !isDropdownOpen && isHovered && 'opacity-100 hover:bg-active',
            !isDropdownOpen && !isHovered && 'opacity-0 pointer-events-none',
          )}
          title="更多选项"
        >
          <MoreHorizontal className="w-4 h-4 text-text-tertiary" />
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
export default memo(function ConversationItem({
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
      className={cn(
        'mx-2 mb-1 px-3 py-1.5 rounded-lg cursor-pointer transition-base outline-none',
        currentConversationId === conv.id ? 'bg-accent-light' : 'hover:bg-hover',
      )}
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
          className="w-full bg-surface-card text-text-primary text-sm px-2 py-1 rounded outline-none border border-accent focus:ring-2 focus:ring-focus-ring"
          onClick={(e) => e.stopPropagation()}
          aria-label="重命名对话标题"
          placeholder="输入新的对话标题"
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
});
