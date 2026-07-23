/**
 * Workspace 状态管理 Hook
 *
 * 管理：路径导航、文件列表、视图模式、loading、CRUD 操作。
 * 面板关闭再打开时保持数据（不清空），静默刷新。
 */

import { useCallback } from 'react';
import type { WorkspaceFileItem } from '../services/workspace';
import type { CategoryFilter } from '../utils/fileCategory';
import { useWorkspaceBrowser } from './workspace/useWorkspaceBrowser';
import { useWorkspaceMutations } from './workspace/useWorkspaceMutations';
import { useWorkspaceUpload } from './workspace/useWorkspaceUpload';
import {
  useSortedWorkspaceItems,
  useWorkspaceViewState,
} from './workspace/useWorkspaceViewState';
import type {
  SortField,
  SortOrder,
  ViewMode,
} from './workspace/types';

export type { ViewMode, SortField, SortOrder } from './workspace/types';

export interface UseWorkspaceReturn {
  /** 当前路径 */
  currentPath: string;
  /** 文件列表 */
  items: WorkspaceFileItem[];
  /** 是否正在加载 */
  loading: boolean;
  /** 视图模式 */
  viewMode: ViewMode;
  /** 排序字段 */
  sortField: SortField;
  /** 排序方向 */
  sortOrder: SortOrder;
  /** 操作中的路径（用于显示 loading 状态） */
  operatingPath: string | null;
  /** 错误信息 */
  error: string | null;
  /** 导航到子目录 */
  navigateTo: (path: string) => void;
  /** 面包屑路径段 */
  breadcrumbs: { label: string; path: string }[];
  /** 刷新当前目录 */
  refresh: () => Promise<void>;
  /** 上传文件（返回 true=全部成功） */
  upload: (files: File[]) => Promise<boolean>;
  /** 删除 */
  remove: (path: string) => Promise<boolean>;
  /** 新建文件夹 */
  mkdir: (name: string) => Promise<boolean>;
  /** 重命名 */
  rename: (oldName: string, newName: string) => Promise<boolean>;
  /** 移动 */
  move: (srcPath: string, destDir: string) => Promise<boolean>;
  /** 切换视图 */
  setViewMode: (mode: ViewMode) => void;
  /** 切换排序（点击同列翻转方向，点击新列重置升序） */
  toggleSort: (field: SortField) => void;
  /** 清除错误 */
  clearError: () => void;
  /** 当前分类筛选（all=全部 / images=图片与视频 / documents=文档） */
  categoryFilter: CategoryFilter;
  /** 切换分类筛选 */
  setCategoryFilter: (filter: CategoryFilter) => void;
  /** 多选模式开关（开启后单击文件即切换选中，无需 Ctrl/Shift） */
  multiSelectMode: boolean;
  /** 切换多选模式 */
  setMultiSelectMode: (on: boolean) => void;
}

export function useWorkspace(): UseWorkspaceReturn {
  const view = useWorkspaceViewState();
  const { setCategoryFilter, setMultiSelectMode } = view;
  const resetPathView = useCallback(() => {
    setCategoryFilter('all');
    setMultiSelectMode(false);
  }, [setCategoryFilter, setMultiSelectMode]);
  const browser = useWorkspaceBrowser(resetPathView);
  const { setError } = browser;
  const uploadState = useWorkspaceUpload(
    browser.currentPath,
    browser.fetchList,
    browser.setError,
  );
  const mutations = useWorkspaceMutations(
    browser.currentPath,
    browser.fetchList,
    browser.setError,
  );
  const sortedItems = useSortedWorkspaceItems(
    browser.items,
    uploadState.uploadingFiles,
    browser.currentPath,
    view.sortField,
    view.sortOrder,
  );
  const clearError = useCallback(() => setError(null), [setError]);

  return {
    currentPath: browser.currentPath,
    items: sortedItems,
    loading: browser.loading,
    viewMode: view.viewMode,
    sortField: view.sortField,
    sortOrder: view.sortOrder,
    operatingPath: mutations.operatingPath,
    error: browser.error,
    navigateTo: browser.navigateTo,
    breadcrumbs: browser.breadcrumbs,
    refresh: browser.refresh,
    upload: uploadState.upload,
    remove: mutations.remove,
    mkdir: mutations.mkdir,
    rename: mutations.rename,
    move: mutations.move,
    setViewMode: view.setViewMode,
    toggleSort: view.toggleSort,
    clearError,
    categoryFilter: view.categoryFilter,
    setCategoryFilter: view.setCategoryFilter,
    multiSelectMode: view.multiSelectMode,
    setMultiSelectMode: view.setMultiSelectMode,
  };
}
