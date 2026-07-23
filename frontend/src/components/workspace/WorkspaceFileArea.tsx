import { Loader2 } from 'lucide-react';
import type { UseWorkspaceReturn } from '../../hooks/useWorkspace';
import type { useFileSelection } from '../../hooks/useFileSelection';
import { rubberBandStyle } from '../../hooks/useRubberBand';
import FileContextMenu from './FileContextMenu';
import WorkspaceDropZone from './WorkspaceDropZone';
import WorkspaceEmptyState from './WorkspaceEmptyState';
import WorkspaceFileGrid from './WorkspaceFileGrid';
import WorkspaceFileList from './WorkspaceFileList';
import type { useWorkspaceItemActions } from './useWorkspaceItemActions';
import type { useWorkspaceSelectionActions } from './useWorkspaceSelectionActions';

interface WorkspaceFileAreaProps {
  workspace: UseWorkspaceReturn;
  selection: ReturnType<typeof useFileSelection>;
  itemActions: ReturnType<typeof useWorkspaceItemActions>;
  selectionActions: ReturnType<typeof useWorkspaceSelectionActions>;
  fileAreaRef: React.RefObject<HTMLDivElement | null>;
  fileInputRef: React.RefObject<HTMLInputElement | null>;
}

export default function WorkspaceFileArea(props: WorkspaceFileAreaProps) {
  const {
    workspace: ws,
    selection,
    itemActions,
    selectionActions,
    fileAreaRef,
    fileInputRef,
  } = props;
  const { filteredItems } = itemActions;

  return (
    <WorkspaceDropZone onDrop={itemActions.handleUpload}>
      <FileContextMenu
        type="blank"
        blankProps={{
          onNewFolder: () => ws.mkdir('新建文件夹'),
          onUpload: () => fileInputRef.current?.click(),
        }}
      >
        <div ref={fileAreaRef} className="relative flex-1 overflow-y-auto select-none">
          {selectionActions.rubberBand.rect && (
            <div style={rubberBandStyle(selectionActions.rubberBand.rect)} />
          )}
          {ws.loading && ws.items.length === 0 ? (
            <div className="flex-1 flex items-center justify-center h-full">
              <Loader2 className="w-8 h-8 text-[var(--s-text-tertiary)] animate-spin" />
            </div>
          ) : ws.items.length === 0 ? (
            <WorkspaceEmptyState />
          ) : filteredItems.length === 0 ? (
            <div className="flex-1 flex flex-col items-center justify-center h-full px-6 text-center">
              <div className="text-4xl mb-3" aria-hidden>📂</div>
              <div className="text-sm text-[var(--s-text-secondary)]">该分类下暂无文件</div>
            </div>
          ) : ws.viewMode === 'list' ? (
            <div className="px-1">
              <WorkspaceFileList
                items={filteredItems}
                currentPath={ws.currentPath}
                selectedPaths={selection.selectedPaths}
                renameTarget={selectionActions.renameTarget}
                sortField={ws.sortField}
                sortOrder={ws.sortOrder}
                onToggleSort={ws.toggleSort}
                onSelect={itemActions.handleSelect}
                onOpen={itemActions.handleOpen}
                onRename={ws.rename}
                onRenameEnd={() => selectionActions.setRenameTarget(null)}
                onDelete={selectionActions.handleDelete}
                onSendToChat={itemActions.handleSendToChat}
                onStartRename={selectionActions.setRenameTarget}
                onMove={ws.move}
                onBatchDownload={itemActions.handleBatchDownload}
                multiSelectMode={ws.multiSelectMode}
              />
            </div>
          ) : (
            <WorkspaceFileGrid
              items={filteredItems}
              currentPath={ws.currentPath}
              selectedPaths={selection.selectedPaths}
              renameTarget={selectionActions.renameTarget}
              onSelect={itemActions.handleSelect}
              onOpen={itemActions.handleOpen}
              onRename={ws.rename}
              onRenameEnd={() => selectionActions.setRenameTarget(null)}
              onDelete={selectionActions.handleDelete}
              onSendToChat={itemActions.handleSendToChat}
              onStartRename={selectionActions.setRenameTarget}
              onMove={ws.move}
              onBatchDownload={itemActions.handleBatchDownload}
              multiSelectMode={ws.multiSelectMode}
            />
          )}
        </div>
      </FileContextMenu>
    </WorkspaceDropZone>
  );
}
