/**
 * 工作区列表模式渲染
 *
 * 包含列头（名称/大小/时间）和文件条目。
 * 每个条目包裹在 FileContextMenu 中提供右键菜单。
 */

import { ChevronUp, ChevronDown } from 'lucide-react';
import WorkspaceFileItem, { getFullPath } from './WorkspaceFileItem';
import FileContextMenu from './FileContextMenu';
import { cn } from '../../utils/cn';
import type { WorkspaceFileItem as FileItemData } from '../../services/workspace';
import type { SortField, SortOrder } from '../../hooks/useWorkspace';
import { downloadFile } from '../../utils/downloadFile';

interface WorkspaceFileListProps {
  items: FileItemData[];
  currentPath: string;
  sortField: SortField;
  sortOrder: SortOrder;
  onToggleSort: (field: SortField) => void;
  selectedPaths: Set<string>;
  renameTarget: string | null;
  onSelect: (path: string, e: React.MouseEvent) => void;
  onOpen: (item: FileItemData) => void;
  onRename: (oldName: string, newName: string) => Promise<boolean>;
  onRenameEnd: () => void;
  onDelete: (path: string) => void;
  onSendToChat?: (item: FileItemData) => void;
  onStartRename: (path: string) => void;
  onMove?: (srcPath: string, destDir: string) => void;
}

export default function WorkspaceFileList({
  items,
  currentPath,
  selectedPaths,
  renameTarget,
  sortField,
  sortOrder,
  onToggleSort,
  onSelect,
  onOpen,
  onRename,
  onRenameEnd,
  onDelete,
  onSendToChat,
  onStartRename,
  onMove,
}: WorkspaceFileListProps) {
  const SortIcon = ({ field }: { field: SortField }) => {
    if (sortField !== field) return null;
    return sortOrder === 'asc'
      ? <ChevronUp className="w-3 h-3 inline ml-0.5" />
      : <ChevronDown className="w-3 h-3 inline ml-0.5" />;
  };

  const colClass = (field: SortField) => cn(
    'cursor-pointer hover:text-[var(--s-text-primary)] select-none transition-colors',
    sortField === field && 'text-[var(--s-text-primary)]',
  );

  return (
    <div className="flex flex-col">
      {/* 列头（可点击排序） */}
      <div className="flex items-center gap-3 px-3 py-1.5 text-xs text-[var(--s-text-tertiary)] border-b border-[var(--s-border-subtle)]">
        <span className="w-5 shrink-0" />
        <span className={cn('flex-1', colClass('name'))} onClick={() => onToggleSort('name')}>名称<SortIcon field="name" /></span>
        <span className={cn('w-16 text-right', colClass('size'))} onClick={() => onToggleSort('size')}>大小<SortIcon field="size" /></span>
        <span className={cn('w-24 text-right', colClass('modified'))} onClick={() => onToggleSort('modified')}>修改时间<SortIcon field="modified" /></span>
      </div>

      {/* 文件列表 */}
      {items.map((item) => {
        const fullPath = getFullPath(currentPath, item.name);
        return (
          <FileContextMenu
            key={item.name}
            type="file"
            fileProps={{
              isDir: item.is_dir,
              hasCdnUrl: !!item.cdn_url,
              selectedCount: selectedPaths.size,
              onOpen: () => onOpen(item),
              onRename: () => onStartRename(fullPath),
              onDownload: () => item.cdn_url && downloadFile(item.cdn_url, item.name),
              onSendToChat: () => onSendToChat?.(item),
              onDelete: () => onDelete(fullPath),
            }}
          >
            <div>
              <WorkspaceFileItem
                item={item}
                currentPath={currentPath}
                mode="list"
                selected={selectedPaths.has(fullPath)}
                onSelect={onSelect}
                onOpen={onOpen}
                onRename={onRename}
                startRename={renameTarget === fullPath}
                onRenameEnd={onRenameEnd}
                onMove={onMove}
                selectedPaths={selectedPaths}
              />
            </div>
          </FileContextMenu>
        );
      })}
    </div>
  );
}
