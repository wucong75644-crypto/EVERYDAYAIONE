/**
 * 工作区列表模式渲染
 *
 * 包含列头（名称/大小/时间）和文件条目。
 * 每个条目包裹在 FileContextMenu 中提供右键菜单。
 */

import WorkspaceFileItem, { getFullPath } from './WorkspaceFileItem';
import FileContextMenu from './FileContextMenu';
import type { WorkspaceFileItem as FileItemData } from '../../services/workspace';
import { downloadFile } from '../../utils/downloadFile';

interface WorkspaceFileListProps {
  items: FileItemData[];
  currentPath: string;
  selectedPaths: Set<string>;
  renameTarget: string | null;
  onSelect: (path: string, e: React.MouseEvent) => void;
  onOpen: (item: FileItemData) => void;
  onRename: (oldName: string, newName: string) => Promise<boolean>;
  onRenameEnd: () => void;
  onDelete: (path: string) => void;
  onSendToChat?: (item: FileItemData) => void;
  onStartRename: (path: string) => void;
}

export default function WorkspaceFileList({
  items,
  currentPath,
  selectedPaths,
  renameTarget,
  onSelect,
  onOpen,
  onRename,
  onRenameEnd,
  onDelete,
  onSendToChat,
  onStartRename,
}: WorkspaceFileListProps) {
  return (
    <div className="flex flex-col">
      {/* 列头 */}
      <div className="flex items-center gap-3 px-3 py-1.5 text-xs text-[var(--s-text-tertiary)] border-b border-[var(--s-border-subtle)]">
        <span className="w-5 shrink-0" />
        <span className="flex-1">名称</span>
        <span className="w-16 text-right">大小</span>
        <span className="w-24 text-right">修改时间</span>
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
              />
            </div>
          </FileContextMenu>
        );
      })}
    </div>
  );
}
