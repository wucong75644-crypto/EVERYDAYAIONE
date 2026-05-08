/**
 * 工作区图标模式渲染
 *
 * 每个条目包裹在 FileContextMenu 中提供右键菜单。
 */

import WorkspaceFileItem, { getFullPath } from './WorkspaceFileItem';
import FileContextMenu from './FileContextMenu';
import type { WorkspaceFileItem as FileItemData } from '../../services/workspace';
import { downloadFile } from '../../utils/downloadFile';

interface WorkspaceFileGridProps {
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
  onMove?: (srcPath: string, destDir: string) => void;
}

export default function WorkspaceFileGrid({
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
  onMove,
}: WorkspaceFileGridProps) {
  return (
    <div className="grid grid-cols-4 sm:grid-cols-5 md:grid-cols-6 lg:grid-cols-8 gap-1 p-2">
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
                mode="grid"
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
