/**
 * 单个对话项组件
 *
 * 包含对话内容显示、任务状态徽章、点击/双击/右键交互
 */

import type { ConversationListItem } from '../../services/conversation';
import { useTaskStore } from '../../stores/useTaskStore';

interface ConversationItemContentProps {
  conv: ConversationListItem;
  currentConversationId: string | null;
}

/**
 * 对话项内容（含任务状态徽章）
 */
export function ConversationItemContent({
  conv,
  currentConversationId,
}: ConversationItemContentProps) {
  const { hasActiveTask, getTask, isRecentlyCompleted } = useTaskStore();
  const isActive = hasActiveTask(conv.id);
  const task = getTask(conv.id);
  const justCompleted = isRecentlyCompleted(conv.id);

  return (
    <>
      <div className="flex items-center gap-2">
        <div
          className={`text-sm truncate flex-1 ${
            currentConversationId === conv.id
              ? 'text-gray-900 font-semibold'
              : 'text-gray-800 font-medium'
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
      </div>
    </>
  );
}

interface ConversationItemProps {
  conv: ConversationListItem;
  currentConversationId: string | null;
  isClicked: boolean;
  isRenaming: boolean;
  renameTitle: string;
  onSelect: () => void;
  onStartRename: () => void;
  onContextMenu: (e: React.MouseEvent) => void;
  onRenameChange: (value: string) => void;
  onRenameSubmit: () => void;
  onRenameCancel: () => void;
}

/**
 * 完整的对话项组件（包含点击、双击、右键交互）
 */
export default function ConversationItem({
  conv,
  currentConversationId,
  isClicked,
  isRenaming,
  renameTitle,
  onSelect,
  onStartRename,
  onContextMenu,
  onRenameChange,
  onRenameSubmit,
  onRenameCancel,
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
      className={`mx-2 mb-1 px-3 py-1.5 rounded-lg cursor-pointer transition-all duration-150 outline-none ${
        currentConversationId === conv.id
          ? 'bg-blue-50'
          : isClicked
          ? 'bg-blue-50 scale-[0.98]'
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
        <ConversationItemContent conv={conv} currentConversationId={currentConversationId} />
      )}
    </div>
  );
}
