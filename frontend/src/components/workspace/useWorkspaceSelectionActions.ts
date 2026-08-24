import { useCallback, useRef, useState } from 'react';
import type { Dispatch, SetStateAction } from 'react';
import toast from 'react-hot-toast';
import type { UseWorkspaceReturn } from '../../hooks/useWorkspace';
import type { useFileSelection } from '../../hooks/useFileSelection';
import { useRubberBand } from '../../hooks/useRubberBand';
import type { Rect } from '../../hooks/useRubberBand';

type Selection = ReturnType<typeof useFileSelection>;

interface SelectionActionOptions {
  workspace: UseWorkspaceReturn;
  selection: Selection;
  fileAreaRef: React.RefObject<HTMLDivElement | null>;
}

interface WorkspaceSelectionActions {
  renameTarget: string | null;
  setRenameTarget: Dispatch<SetStateAction<string | null>>;
  deleteTarget: string | null;
  setDeleteTarget: Dispatch<SetStateAction<string | null>>;
  deleteLoading: boolean;
  handleDelete: (path: string) => void;
  handleDeleteConfirm: () => Promise<void>;
  handleToggleMultiSelect: () => void;
  rubberBand: { rect: Rect | null; isDragging: boolean };
}

export function useWorkspaceSelectionActions(
  options: SelectionActionOptions,
): WorkspaceSelectionActions {
  const { workspace, selection, fileAreaRef } = options;
  const { multiSelectMode, setMultiSelectMode, remove } = workspace;
  const [renameTarget, setRenameTarget] = useState<string | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<string | null>(null);
  const [deleteLoading, setDeleteLoading] = useState(false);
  const additiveBaselineRef = useRef<string[]>([]);

  const handleDelete = useCallback((path: string) => {
    setDeleteTarget(selection.selectedCount > 1
      ? `batch:${selection.selectedCount}`
      : path);
  }, [selection.selectedCount]);

  const handleDeleteConfirm = useCallback(async () => {
    if (!deleteTarget) return;
    setDeleteLoading(true);
    const paths = deleteTarget.startsWith('batch:')
      ? Array.from(selection.selectedPaths)
      : [deleteTarget];
    let successCount = 0;
    for (const path of paths) {
      if (await remove(path)) successCount++;
    }
    const allSucceeded = successCount === paths.length;
    const message = paths.length === 1
      ? allSucceeded ? '已删除' : '删除失败'
      : allSucceeded ? `已删除 ${successCount} 项` : `删除了 ${successCount}/${paths.length} 项`;
    toast[allSucceeded ? 'success' : 'error'](message);
    if (paths.length > 1) selection.clear();
    setDeleteLoading(false);
    setDeleteTarget(null);
  }, [deleteTarget, remove, selection]);

  const handleToggleMultiSelect = useCallback(() => {
    const next = !multiSelectMode;
    setMultiSelectMode(next);
    if (!next) selection.clear();
  }, [multiSelectMode, selection, setMultiSelectMode]);

  const handleRubberSelect = useCallback((paths: string[], additive: boolean) => {
    if (!additive) {
      selection.selectAll(paths);
      return;
    }
    selection.selectAll(Array.from(new Set([
      ...additiveBaselineRef.current,
      ...paths,
    ])));
  }, [selection]);

  const rubberBand = useRubberBand({
    containerRef: fileAreaRef,
    onSelectionChange: handleRubberSelect,
    onDragStart: useCallback(() => {
      additiveBaselineRef.current = Array.from(selection.selectedPaths);
    }, [selection.selectedPaths]),
    onEmptyClick: useCallback(() => selection.clear(), [selection]),
    enabled: !multiSelectMode,
  });

  return {
    renameTarget,
    setRenameTarget,
    deleteTarget,
    setDeleteTarget,
    deleteLoading,
    handleDelete,
    handleDeleteConfirm,
    handleToggleMultiSelect,
    rubberBand,
  };
}
