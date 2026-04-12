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

export type ViewMode = 'list' | 'grid';

const VIEW_MODE_KEY = 'workspace_view_mode';

function loadViewMode(): ViewMode {
  try {
    const saved = localStorage.getItem(VIEW_MODE_KEY);
    if (saved === 'grid') return 'grid';
  } catch { /* ignore */ }
  return 'list';
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
  /** 清除错误 */
  clearError: () => void;
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
  const [viewMode, setViewModeState] = useState<ViewMode>(loadViewMode);

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

  // 首次加载 + 路径变化时获取列表
  useEffect(() => {
    fetchList(currentPath);
  }, [currentPath, fetchList]);

  const navigateTo = useCallback((path: string) => {
    setCurrentPath(path);
  }, []);

  const refresh = useCallback(async () => {
    await fetchList(currentPath);
  }, [currentPath, fetchList]);

  // 面包屑（memoized，避免子组件不必要的重渲染）
  const breadcrumbs = useMemo(() => {
    if (currentPath === '.') return [{ label: '工作区', path: '.' }];
    const parts = currentPath.split('/').filter(Boolean);
    const crumbs = [{ label: '工作区', path: '.' }];
    let accumulated = '';
    for (const part of parts) {
      accumulated = accumulated ? `${accumulated}/${part}` : part;
      crumbs.push({ label: part, path: accumulated });
    }
    return crumbs;
  }, [currentPath]);

  // 上传文件（返回 true=全部成功，false=有失败）
  const upload = useCallback(async (files: File[]): Promise<boolean> => {
    setError(null);
    for (const file of files) {
      try {
        await uploadToWorkspace(file, currentPath);
      } catch (err) {
        const msg = err instanceof Error ? err.message : '上传失败';
        setError(msg);
        logger.error('useWorkspace', `上传失败: ${file.name}`, err);
        await fetchList(currentPath); // 已上传的部分也要刷新
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

  // 切换视图模式
  const setViewMode = useCallback((mode: ViewMode) => {
    setViewModeState(mode);
    try {
      localStorage.setItem(VIEW_MODE_KEY, mode);
    } catch { /* ignore */ }
  }, []);

  const clearError = useCallback(() => setError(null), []);

  // 排序后的文件列表（文件夹在前，各自按名称排序）
  const sortedItems = useMemo(() => {
    return [...items].sort((a, b) => {
      if (a.is_dir !== b.is_dir) return a.is_dir ? -1 : 1;
      return a.name.localeCompare(b.name);
    });
  }, [items]);

  return {
    currentPath,
    items: sortedItems,
    loading,
    viewMode,
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
    clearError,
  };
}
