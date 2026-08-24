import { useCallback, useMemo, useState } from 'react';
import type { WorkspaceFileItem } from '../../services/workspace';
import type { CategoryFilter } from '../../utils/fileCategory';
import type {
  SortField,
  SortOrder,
  ViewMode,
  WorkspaceViewState,
} from './types';

const VIEW_MODE_KEY = 'workspace_view_mode';
const SORT_FIELD_KEY = 'workspace_sort_field';
const SORT_ORDER_KEY = 'workspace_sort_order';

export function useWorkspaceViewState(): WorkspaceViewState {
  const [userViewMode, setUserViewMode] = useState<ViewMode>(loadViewMode);
  const [sortField, setSortField] = useState<SortField>(loadSortField);
  const [sortOrder, setSortOrder] = useState<SortOrder>(loadSortOrder);
  const [categoryFilter, setCategoryFilter] = useState<CategoryFilter>('all');
  const [multiSelectMode, setMultiSelectMode] = useState(false);
  const viewMode: ViewMode = categoryFilter === 'images' ? 'grid' : userViewMode;

  const changeViewMode = useCallback((mode: ViewMode) => {
    setUserViewMode(mode);
    persist(VIEW_MODE_KEY, mode);
  }, []);

  const toggleSort = useCallback((field: SortField) => {
    setSortField((previous) => {
      if (previous === field) {
        setSortOrder((order) => {
          const next = order === 'asc' ? 'desc' : 'asc';
          persist(SORT_ORDER_KEY, next);
          return next;
        });
        return previous;
      }
      setSortOrder('asc');
      persist(SORT_FIELD_KEY, field);
      persist(SORT_ORDER_KEY, 'asc');
      return field;
    });
  }, []);

  return {
    viewMode,
    sortField,
    sortOrder,
    categoryFilter,
    multiSelectMode,
    setViewMode: changeViewMode,
    toggleSort,
    setCategoryFilter,
    setMultiSelectMode,
  };
}

export function useSortedWorkspaceItems(
  items: WorkspaceFileItem[],
  uploadingFiles: Map<string, WorkspaceFileItem>,
  currentPath: string,
  sortField: SortField,
  sortOrder: SortOrder,
): WorkspaceFileItem[] {
  return useMemo(() => {
    const existing = new Set(items.map((item) => item.name));
    const pending = Array.from(uploadingFiles.values()).filter(
      (item) => !existing.has(item.name) && item._uploadPath === currentPath,
    );
    return [...items, ...pending].sort(
      (left, right) => compareItems(left, right, sortField, sortOrder),
    );
  }, [items, uploadingFiles, currentPath, sortField, sortOrder]);
}

function compareItems(
  left: WorkspaceFileItem,
  right: WorkspaceFileItem,
  field: SortField,
  order: SortOrder,
): number {
  if (left.is_dir !== right.is_dir) return left.is_dir ? -1 : 1;
  const direction = order === 'asc' ? 1 : -1;
  if (field === 'name') return direction * left.name.localeCompare(right.name);
  if (field === 'size') return direction * ((left.size || 0) - (right.size || 0));
  return direction * (Number(left.modified || 0) - Number(right.modified || 0));
}

function loadViewMode(): ViewMode {
  return readPreference(VIEW_MODE_KEY) === 'grid' ? 'grid' : 'list';
}

function loadSortField(): SortField {
  const saved = readPreference(SORT_FIELD_KEY);
  return saved === 'name' || saved === 'size' || saved === 'modified' ? saved : 'modified';
}

function loadSortOrder(): SortOrder {
  return readPreference(SORT_ORDER_KEY) === 'asc' ? 'asc' : 'desc';
}

function readPreference(key: string): string | null {
  try { return localStorage.getItem(key); } catch { return null; }
}

function persist(key: string, value: string): void {
  try { localStorage.setItem(key, value); } catch { /* ignore unavailable storage */ }
}
