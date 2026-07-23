import type { Dispatch, SetStateAction } from 'react';
import type { WorkspaceFileItem } from '../../services/workspace';
import type { CategoryFilter } from '../../utils/fileCategory';

export type ViewMode = 'list' | 'grid';
export type SortField = 'name' | 'size' | 'modified';
export type SortOrder = 'asc' | 'desc';

export type FetchWorkspaceList = (path: string, silent?: boolean) => Promise<void>;
export type SetWorkspaceError = Dispatch<SetStateAction<string | null>>;

export interface WorkspaceBrowserState {
  currentPath: string;
  items: WorkspaceFileItem[];
  loading: boolean;
  error: string | null;
  navigateTo: (path: string) => void;
  breadcrumbs: { label: string; path: string }[];
  refresh: () => Promise<void>;
  fetchList: FetchWorkspaceList;
  setError: SetWorkspaceError;
}

export interface WorkspaceViewState {
  viewMode: ViewMode;
  sortField: SortField;
  sortOrder: SortOrder;
  categoryFilter: CategoryFilter;
  multiSelectMode: boolean;
  setViewMode: (mode: ViewMode) => void;
  toggleSort: (field: SortField) => void;
  setCategoryFilter: (filter: CategoryFilter) => void;
  setMultiSelectMode: (on: boolean) => void;
}
