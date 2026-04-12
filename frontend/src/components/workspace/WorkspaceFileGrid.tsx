/**
 * 工作区图标模式渲染
 */

import WorkspaceFileItem from './WorkspaceFileItem';
import type { WorkspaceFileItem as FileItemData } from '../../services/workspace';

interface WorkspaceFileGridProps {
  items: FileItemData[];
  currentPath: string;
  onNavigate: (path: string) => void;
  onRename: (oldName: string, newName: string) => Promise<boolean>;
  onDelete: (path: string) => void;
  onPreview: (item: FileItemData) => void;
  onSendToChat?: (item: FileItemData) => void;
}

export default function WorkspaceFileGrid({
  items,
  currentPath,
  onNavigate,
  onRename,
  onDelete,
  onPreview,
  onSendToChat,
}: WorkspaceFileGridProps) {
  return (
    <div className="grid grid-cols-4 sm:grid-cols-5 md:grid-cols-6 lg:grid-cols-8 gap-1 p-2">
      {/* 已由 useWorkspace 排序 */}
      {items.map((item) => (
        <WorkspaceFileItem
          key={item.name}
          item={item}
          currentPath={currentPath}
          mode="grid"
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
