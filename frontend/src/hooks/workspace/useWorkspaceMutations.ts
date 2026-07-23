import { useCallback, useState } from 'react';
import {
  deleteWorkspaceItem,
  mkdirWorkspace,
  moveWorkspaceItem,
  renameWorkspaceItem,
} from '../../services/workspace';
import type { FetchWorkspaceList, SetWorkspaceError } from './types';

export function useWorkspaceMutations(
  currentPath: string,
  fetchList: FetchWorkspaceList,
  setError: SetWorkspaceError,
) {
  const [operatingPath, setOperatingPath] = useState<string | null>(null);

  const remove = useCallback(async (path: string): Promise<boolean> => {
    setOperatingPath(path);
    try {
      await deleteWorkspaceItem(path);
      await fetchList(currentPath);
      return true;
    } catch (err) {
      setError(toMessage(err, '删除失败'));
      return false;
    } finally {
      setOperatingPath(null);
    }
  }, [currentPath, fetchList, setError]);

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
      setError(toMessage(err, '创建文件夹失败'));
      return false;
    }
  }, [currentPath, fetchList, setError]);

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
      setError(toMessage(err, '重命名失败'));
      return false;
    }
  }, [currentPath, fetchList, setError]);

  const move = useCallback(async (srcPath: string, destDir: string): Promise<boolean> => {
    try {
      await moveWorkspaceItem(srcPath, destDir);
      await fetchList(currentPath);
      return true;
    } catch (err) {
      setError(toMessage(err, '移动失败'));
      return false;
    }
  }, [currentPath, fetchList, setError]);

  return { operatingPath, remove, mkdir, rename, move };
}

function isValidName(name: string): boolean {
  if (!name || !name.trim()) return false;
  if (name.includes('/') || name.includes('\\') || name.includes('\0')) return false;
  return name !== '.' && name !== '..';
}

function toMessage(error: unknown, fallback: string): string {
  return error instanceof Error ? error.message : fallback;
}
