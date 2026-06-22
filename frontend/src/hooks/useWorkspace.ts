/**
 * Workspace 状态管理 Hook
 *
 * 管理：路径导航、文件列表、视图模式、loading、CRUD 操作。
 * 面板关闭再打开时保持数据（不清空），静默刷新。
 */

import { useState, useCallback, useEffect, useRef, useMemo } from 'react';
import { logger } from '../utils/logger';
import {
  listWorkspace,
  uploadToWorkspace,
  deleteWorkspaceItem,
  mkdirWorkspace,
  renameWorkspaceItem,
  moveWorkspaceItem,
  type WorkspaceFileItem,
} from '../services/workspace';
import type { CategoryFilter } from '../utils/fileCategory';

export type ViewMode = 'list' | 'grid';
export type SortField = 'name' | 'size' | 'modified';
export type SortOrder = 'asc' | 'desc';

const VIEW_MODE_KEY = 'workspace_view_mode';
const SORT_FIELD_KEY = 'workspace_sort_field';
const SORT_ORDER_KEY = 'workspace_sort_order';

function loadViewMode(): ViewMode {
  try {
    const saved = localStorage.getItem(VIEW_MODE_KEY);
    if (saved === 'grid') return 'grid';
  } catch { /* ignore */ }
  return 'list';
}

function loadSortField(): SortField {
  try {
    const saved = localStorage.getItem(SORT_FIELD_KEY);
    if (saved === 'name' || saved === 'size' || saved === 'modified') return saved;
  } catch { /* ignore */ }
  return 'modified';
}

function loadSortOrder(): SortOrder {
  try {
    const saved = localStorage.getItem(SORT_ORDER_KEY);
    if (saved === 'asc' || saved === 'desc') return saved;
  } catch { /* ignore */ }
  return 'desc';
}

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

/** 校验文件/文件夹名称（前端防御层，后端 resolve_safe_path 做最终校验） */
function isValidName(name: string): boolean {
  if (!name || !name.trim()) return false;
  if (name.includes('/') || name.includes('\\') || name.includes('\0')) return false;
  if (name === '.' || name === '..') return false;
  return true;
}

export function useWorkspace(): UseWorkspaceReturn {
  const [currentPath, setCurrentPath] = useState('.');
  const [items, setItems] = useState<WorkspaceFileItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [operatingPath, setOperatingPath] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [userViewMode, setUserViewModeState] = useState<ViewMode>(loadViewMode);
  const [sortField, setSortField] = useState<SortField>(loadSortField);
  const [sortOrder, setSortOrder] = useState<SortOrder>(loadSortOrder);
  const [categoryFilter, setCategoryFilterState] = useState<CategoryFilter>('all');
  const [multiSelectMode, setMultiSelectModeState] = useState<boolean>(false);

  // 「图片与视频」Tab 强制 grid；其他 Tab 用用户偏好（实现自动联动 + 恢复）
  const viewMode: ViewMode = categoryFilter === 'images' ? 'grid' : userViewMode;

  // 防止快速切换目录时的竞态
  const fetchSeqRef = useRef(0);

  const fetchList = useCallback(async (path: string, silent = false) => {
    fetchSeqRef.current += 1;
    const seq = fetchSeqRef.current;

    if (!silent) setLoading(true);
    setError(null);

    try {
      const result = await listWorkspace(path);
      // 竞态保护
      if (seq !== fetchSeqRef.current) return;
      setItems(result.items);
    } catch (err) {
      if (seq !== fetchSeqRef.current) return;
      const msg = err instanceof Error ? err.message : '加载文件列表失败';
      setError(msg);
      logger.error('useWorkspace', '列表加载失败', err);
    } finally {
      if (seq === fetchSeqRef.current) {
        setLoading(false);
      }
    }
  }, []);

  // 首次加载 + 路径变化时获取列表 + 重置 Tab 到「全部」+ 退出多选模式
  useEffect(() => {
    fetchList(currentPath);
    setCategoryFilterState('all');
    setMultiSelectModeState(false);
  }, [currentPath, fetchList]);

  // Agent 文件操作（code_execute 生成/删除）后自动刷新
  useEffect(() => {
    const handler = () => { fetchList(currentPath, true); };
    window.addEventListener('workspace:changed', handler);
    return () => window.removeEventListener('workspace:changed', handler);
  }, [currentPath, fetchList]);

  const navigateTo = useCallback((path: string) => {
    setCurrentPath(path);
  }, []);

  const refresh = useCallback(async () => {
    await fetchList(currentPath);
  }, [currentPath, fetchList]);

  // 面包屑（memoized，避免子组件不必要的重渲染）
  const breadcrumbs = useMemo(() => {
    if (currentPath === '.') return [{ label: '根目录', path: '.' }];
    const parts = currentPath.split('/').filter(Boolean);
    const crumbs = [{ label: '根目录', path: '.' }];
    let accumulated = '';
    for (const part of parts) {
      accumulated = accumulated ? `${accumulated}/${part}` : part;
      crumbs.push({ label: part, path: accumulated });
    }
    return crumbs;
  }, [currentPath]);

  // 上传中的文件（占位 + 进度）
  const [uploadingFiles, setUploadingFiles] = useState<Map<string, WorkspaceFileItem>>(new Map());

  const upload = useCallback(async (files: File[]): Promise<boolean> => {
    setError(null);

    // 立即添加占位项
    const placeholders = new Map<string, WorkspaceFileItem>();
    for (const file of files) {
      placeholders.set(file.name, {
        name: file.name,
        is_dir: false,
        size: file.size,
        modified: String(Math.floor(Date.now() / 1000)),
        cdn_url: null,
        mime_type: file.type || null,
        uploadProgress: 0,
        _uploadPath: currentPath,
      });
    }
    setUploadingFiles((prev) => new Map([...prev, ...placeholders]));

    for (const file of files) {
      try {
        await uploadToWorkspace(file, currentPath, (percent) => {
          setUploadingFiles((prev) => {
            const next = new Map(prev);
            const item = next.get(file.name);
            if (item) next.set(file.name, { ...item, uploadProgress: percent });
            return next;
          });
        });
        // 单个完成，移除占位
        setUploadingFiles((prev) => {
          const next = new Map(prev);
          next.delete(file.name);
          return next;
        });
      } catch (err) {
        const msg = err instanceof Error ? err.message : '上传失败';
        setError(msg);
        logger.error('useWorkspace', `上传失败: ${file.name}`, err);
        // 失败也移除占位
        setUploadingFiles((prev) => {
          const next = new Map(prev);
          next.delete(file.name);
          return next;
        });
        await fetchList(currentPath);
        return false;
      }
    }
    await fetchList(currentPath);
    return true;
  }, [currentPath, fetchList]);

  // 删除
  const remove = useCallback(async (path: string): Promise<boolean> => {
    setOperatingPath(path);
    try {
      await deleteWorkspaceItem(path);
      await fetchList(currentPath);
      return true;
    } catch (err) {
      const msg = err instanceof Error ? err.message : '删除失败';
      setError(msg);
      return false;
    } finally {
      setOperatingPath(null);
    }
  }, [currentPath, fetchList]);

  // 新建文件夹
  const mkdir = useCallback(async (name: string): Promise<boolean> => {
    if (!isValidName(name)) {
      setError('文件夹名称无效（不能包含 / \\ 等特殊字符）');
      return false;
    }
    const fullPath = currentPath === '.' ? name : `${currentPath}/${name}`;
    try {
      await mkdirWorkspace(fullPath);
      await fetchList(currentPath);
      return true;
    } catch (err) {
      const msg = err instanceof Error ? err.message : '创建文件夹失败';
      setError(msg);
      return false;
    }
  }, [currentPath, fetchList]);

  // 重命名
  const rename = useCallback(async (oldName: string, newName: string): Promise<boolean> => {
    if (!isValidName(newName)) {
      setError('文件名无效（不能包含 / \\ 等特殊字符）');
      return false;
    }
    const dir = currentPath === '.' ? '' : `${currentPath}/`;
    try {
      await renameWorkspaceItem(`${dir}${oldName}`, `${dir}${newName}`);
      await fetchList(currentPath);
      return true;
    } catch (err) {
      const msg = err instanceof Error ? err.message : '重命名失败';
      setError(msg);
      return false;
    }
  }, [currentPath, fetchList]);

  // 移动
  const move = useCallback(async (srcPath: string, destDir: string): Promise<boolean> => {
    try {
      await moveWorkspaceItem(srcPath, destDir);
      await fetchList(currentPath);
      return true;
    } catch (err) {
      const msg = err instanceof Error ? err.message : '移动失败';
      setError(msg);
      return false;
    }
  }, [currentPath, fetchList]);

  // 切换视图模式（更新用户偏好 + 持久化；images Tab 下展示值仍受 categoryFilter 控制）
  const setViewMode = useCallback((mode: ViewMode) => {
    setUserViewModeState(mode);
    try {
      localStorage.setItem(VIEW_MODE_KEY, mode);
    } catch { /* ignore */ }
  }, []);

  const setCategoryFilter = useCallback((filter: CategoryFilter) => {
    setCategoryFilterState(filter);
  }, []);

  const setMultiSelectMode = useCallback((on: boolean) => {
    setMultiSelectModeState(on);
  }, []);

  const clearError = useCallback(() => setError(null), []);

  // 切换排序：同字段翻转方向，新字段重置升序；全程持久化
  const toggleSort = useCallback((field: SortField) => {
    setSortField((prevField) => {
      if (prevField === field) {
        setSortOrder((o) => {
          const next: SortOrder = o === 'asc' ? 'desc' : 'asc';
          try { localStorage.setItem(SORT_ORDER_KEY, next); } catch { /* ignore */ }
          return next;
        });
        return prevField;
      }
      setSortOrder('asc');
      try {
        localStorage.setItem(SORT_FIELD_KEY, field);
        localStorage.setItem(SORT_ORDER_KEY, 'asc');
      } catch { /* ignore */ }
      return field;
    });
  }, []);

  // 排序后的文件列表（文件夹始终在前）+ 合并上传中占位
  const sortedItems = useMemo(() => {
    const existing = new Set(items.map((i) => i.name));
    const merged = [...items, ...Array.from(uploadingFiles.values()).filter((u) => !existing.has(u.name) && u._uploadPath === currentPath)];
    const dir = sortOrder === 'asc' ? 1 : -1;
    return merged.sort((a, b) => {
      // 文件夹始终在前
      if (a.is_dir !== b.is_dir) return a.is_dir ? -1 : 1;
      // 同类型内按字段排序
      if (sortField === 'name') return dir * a.name.localeCompare(b.name);
      if (sortField === 'size') return dir * ((a.size || 0) - (b.size || 0));
      if (sortField === 'modified') return dir * (Number(a.modified || 0) - Number(b.modified || 0));
      return 0;
    });
  }, [items, uploadingFiles, sortField, sortOrder, currentPath]);

  return {
    currentPath,
    items: sortedItems,
    loading,
    viewMode,
    sortField,
    sortOrder,
    operatingPath,
    error,
    navigateTo,
    breadcrumbs,
    refresh,
    upload,
    remove,
    mkdir,
    rename,
    move,
    setViewMode,
    toggleSort,
    clearError,
    categoryFilter,
    setCategoryFilter,
    multiSelectMode,
    setMultiSelectMode,
  };
}
