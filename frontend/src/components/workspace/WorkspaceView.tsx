/**
 * 工作区主视图
 *
 * 只负责组合目录状态、交互控制与展示组件。
 */

import { useCallback, useRef } from 'react';
import { useWorkspace } from '../../hooks/useWorkspace';
import { useFileSelection } from '../../hooks/useFileSelection';
import { usePreview } from '../../preview/usePreview';
import PreviewHost from '../../preview/PreviewHost';
import { useChatAttachmentContext } from '../chat/attachments/ChatAttachmentContext';
import WorkspaceCategoryTabs from './WorkspaceCategoryTabs';
import WorkspaceDeleteDialog from './WorkspaceDeleteDialog';
import WorkspaceFileArea from './WorkspaceFileArea';
import WorkspaceHeader from './WorkspaceHeader';
import { useWorkspaceItemActions } from './useWorkspaceItemActions';
import { useWorkspaceKeyboard } from './useWorkspaceKeyboard';
import { useWorkspaceSelectionActions } from './useWorkspaceSelectionActions';

interface WorkspaceViewProps {
  onBack: () => void;
}

export default function WorkspaceView({ onBack }: WorkspaceViewProps) {
  const { addWorkspaceFile } = useChatAttachmentContext();
  const workspace = useWorkspace();
  const selection = useFileSelection();
  const preview = usePreview();
  const fileInputRef = useRef<HTMLInputElement>(null);
  const fileAreaRef = useRef<HTMLDivElement>(null);

  const selectionActions = useWorkspaceSelectionActions({
    workspace,
    selection,
    fileAreaRef,
  });
  const { setRenameTarget } = selectionActions;
  const { currentPath, navigateTo: navigateWorkspace } = workspace;
  const { clear: clearSelection } = selection;
  const navigateTo = useCallback((path: string) => {
    if (path === currentPath) return;
    clearSelection();
    setRenameTarget(null);
    navigateWorkspace(path);
  }, [clearSelection, currentPath, navigateWorkspace, setRenameTarget]);
  const itemActions = useWorkspaceItemActions({
    workspace,
    selection,
    preview,
    addWorkspaceFile,
    navigateTo,
  });

  useWorkspaceKeyboard({
    workspace,
    selection,
    orderedPaths: itemActions.orderedPaths,
    renameTarget: selectionActions.renameTarget,
    deleteTarget: selectionActions.deleteTarget,
    previewOpen: preview.isOpen,
    openItem: itemActions.handleOpen,
    setRenameTarget,
    setDeleteTarget: selectionActions.setDeleteTarget,
  });

  return (
    <div className="flex-1 flex flex-col min-h-0 bg-[var(--s-surface-base)]" tabIndex={-1}>
      <WorkspaceHeader
        breadcrumbs={workspace.breadcrumbs}
        viewMode={workspace.viewMode}
        onBack={onBack}
        onNavigate={navigateTo}
        onViewModeChange={workspace.setViewMode}
        onUpload={itemActions.handleUpload}
        onMkdir={workspace.mkdir}
      />
      <WorkspaceCategoryTabs
        value={workspace.categoryFilter}
        onChange={workspace.setCategoryFilter}
        multiSelectMode={workspace.multiSelectMode}
        onToggleMultiSelect={selectionActions.handleToggleMultiSelect}
        selectedCount={selection.selectedCount}
        onBatchDownload={itemActions.handleBatchDownloadAll}
      />
      {workspace.error && (
        <div className="mx-4 mt-2 px-3 py-2 text-sm bg-[var(--s-error-soft)] text-[var(--s-error)] rounded-[var(--s-radius-control)] flex items-center justify-between">
          <span>{workspace.error}</span>
          <div className="flex items-center gap-3 shrink-0 ml-2">
            <button type="button" onClick={workspace.refresh} className="text-[var(--s-error)] hover:underline text-xs">重试</button>
            <button type="button" onClick={workspace.clearError} className="text-[var(--s-error)] hover:underline text-xs">关闭</button>
          </div>
        </div>
      )}
      <WorkspaceFileArea
        workspace={workspace}
        selection={selection}
        itemActions={itemActions}
        selectionActions={selectionActions}
        fileAreaRef={fileAreaRef}
        fileInputRef={fileInputRef}
      />
      <input
        ref={fileInputRef}
        type="file"
        multiple
        onChange={(event) => {
          if (event.target.files) {
            itemActions.handleUpload(Array.from(event.target.files));
          }
          event.target.value = '';
        }}
        className="hidden"
      />
      <WorkspaceDeleteDialog
        target={selectionActions.deleteTarget}
        loading={selectionActions.deleteLoading}
        onClose={() => selectionActions.setDeleteTarget(null)}
        onConfirm={selectionActions.handleDeleteConfirm}
      />
      <PreviewHost
        state={preview.state}
        onClose={preview.close}
        onIndexChange={preview.setIndex}
      />
    </div>
  );
}
