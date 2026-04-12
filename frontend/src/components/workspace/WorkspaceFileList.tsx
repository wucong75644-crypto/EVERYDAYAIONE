/**
 * 工作区列表模式渲染
 */

import WorkspaceFileItem from './WorkspaceFileItem';
import type { WorkspaceFileItem as FileItemData } from '../../services/workspace';

interface WorkspaceFileListProps {
  items: FileItemData[];
  currentPath: string;
  onNavigate: (path: string) => void;
  onRename: (oldName: string, newName: string) => Promise<boolean>;
  onDelete: (path: string) => void;
  onPreview: (item: FileItemData) => void;
  onSendToChat?: (item: FileItemData) => void;
}

export default function WorkspaceFileList({
  items,
  currentPath,
  onNavigate,
  onRename,
  onDelete,
  onPreview,
  onSendToChat,
}: WorkspaceFileListProps) {
  return (
    <div className="flex flex-col">
      {/* 列头 */}
      <div className="flex items-center gap-3 px-3 py-1.5 text-xs text-[var(--s-text-tertiary)] border-b border-[var(--s-border-subtle)]">
        <span className="w-5 shrink-0" />
        <span className="flex-1">名称</span>
        <span className="w-16 text-right">大小</span>
        <span className="w-24 text-right">修改时间</span>
        <span className="w-8" />
      </div>

      {/* 文件列表（已由 useWorkspace 排序） */}
      {items.map((item) => (
        <WorkspaceFileItem
          key={item.name}
          item={item}
          currentPath={currentPath}
          mode="list"
          onNavigate={onNavigate}
          onRename={onRename}
          onDelete={onDelete}
          onPreview={onPreview}
          onSendToChat={onSendToChat}
        />
      ))}
    </div>
  );
}
