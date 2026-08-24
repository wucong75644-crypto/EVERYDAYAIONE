import { useEffect } from 'react';
import type { UseWorkspaceReturn } from '../../hooks/useWorkspace';
import type { useFileSelection } from '../../hooks/useFileSelection';
import type { WorkspaceFileItem } from '../../services/workspace';
import { getFullPath } from './WorkspaceFileItem';

type Selection = ReturnType<typeof useFileSelection>;

interface KeyboardOptions {
  workspace: UseWorkspaceReturn;
  selection: Selection;
  orderedPaths: string[];
  renameTarget: string | null;
  deleteTarget: string | null;
  previewOpen: boolean;
  openItem: (item: WorkspaceFileItem) => void;
  setRenameTarget: (path: string | null) => void;
  setDeleteTarget: (path: string | null) => void;
}

export function useWorkspaceKeyboard(options: KeyboardOptions): void {
  const {
    workspace, selection, orderedPaths, renameTarget, deleteTarget,
    previewOpen, openItem, setRenameTarget, setDeleteTarget,
  } = options;
  const {
    items, currentPath, multiSelectMode, setMultiSelectMode,
  } = workspace;

  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if (renameTarget || deleteTarget || previewOpen) return;
      const tag = (event.target as HTMLElement).tagName;
      if (tag === 'INPUT' || tag === 'TEXTAREA') return;
      if ((event.ctrlKey || event.metaKey) && event.key === 'a') {
        event.preventDefault();
        selection.selectAll(orderedPaths);
      } else if (event.key === 'Escape') {
        selection.clear();
        if (multiSelectMode) setMultiSelectMode(false);
      } else if (selection.hasSelection) {
        handleSelectionKey(
          event, selection, items, currentPath, openItem,
          setRenameTarget, setDeleteTarget,
        );
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [
    currentPath, deleteTarget, items, multiSelectMode, openItem, orderedPaths,
    previewOpen, renameTarget, selection, setDeleteTarget, setMultiSelectMode,
    setRenameTarget,
  ]);
}

function handleSelectionKey(
  event: KeyboardEvent,
  selection: Selection,
  items: WorkspaceFileItem[],
  currentPath: string,
  openItem: (item: WorkspaceFileItem) => void,
  setRenameTarget: (path: string | null) => void,
  setDeleteTarget: (path: string | null) => void,
): void {
  const selected = Array.from(selection.selectedPaths);
  if (event.key === 'Delete' || event.key === 'Backspace') {
    event.preventDefault();
    setDeleteTarget(selection.selectedCount > 1
      ? `batch:${selection.selectedCount}`
      : selected[0]);
  } else if (event.key === 'F2' && selection.selectedCount === 1) {
    event.preventDefault();
    setRenameTarget(selected[0]);
  } else if (event.key === 'Enter' && selection.selectedCount === 1) {
    event.preventDefault();
    const item = items.find(
      (entry) => getFullPath(currentPath, entry.name) === selected[0],
    );
    if (item) openItem(item);
  }
}
