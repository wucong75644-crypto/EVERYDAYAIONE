import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { listWorkspace, type WorkspaceFileItem } from '../../services/workspace';
import { logger } from '../../utils/logger';
import type { WorkspaceBrowserState } from './types';

export function useWorkspaceBrowser(
  onPathChange: () => void,
): WorkspaceBrowserState {
  const [currentPath, setCurrentPath] = useState('.');
  const [items, setItems] = useState<WorkspaceFileItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const fetchSeqRef = useRef(0);
  const fetchAbortRef = useRef<AbortController | null>(null);
  const activePathRef = useRef('.');

  const fetchList = useCallback(async (path: string, silent = false) => {
    if (path !== activePathRef.current) return;
    fetchAbortRef.current?.abort();
    const controller = new AbortController();
    fetchAbortRef.current = controller;
    const seq = ++fetchSeqRef.current;
    if (!silent) setLoading(true);
    setError(null);

    try {
      const result = await listWorkspace(path, controller.signal);
      if (seq !== fetchSeqRef.current || path !== activePathRef.current) return;
      if (result.path !== path) throw new Error('工作区返回了不匹配的目录');
      setItems(result.items);
    } catch (err) {
      if (
        controller.signal.aborted ||
        seq !== fetchSeqRef.current ||
        path !== activePathRef.current
      ) return;
      setError(err instanceof Error ? err.message : '加载文件列表失败');
      logger.error('useWorkspace', '列表加载失败', err);
    } finally {
      if (seq === fetchSeqRef.current && path === activePathRef.current) {
        setLoading(false);
      }
    }
  }, []);

  useEffect(() => () => fetchAbortRef.current?.abort(), []);

  useEffect(() => {
    fetchList(currentPath);
  }, [currentPath, fetchList]);

  useEffect(() => {
    const handler = () => { fetchList(currentPath, true); };
    window.addEventListener('workspace:changed', handler);
    return () => window.removeEventListener('workspace:changed', handler);
  }, [currentPath, fetchList]);

  const navigateTo = useCallback((path: string) => {
    if (path === currentPath) return;
    activePathRef.current = path;
    fetchAbortRef.current?.abort();
    setItems([]);
    setLoading(true);
    setError(null);
    onPathChange();
    setCurrentPath(path);
  }, [currentPath, onPathChange]);

  const refresh = useCallback(
    () => fetchList(currentPath),
    [currentPath, fetchList],
  );
  const isActivePath = useCallback(
    (path: string) => path === activePathRef.current,
    [],
  );

  const breadcrumbs = useMemo(() => buildBreadcrumbs(currentPath), [currentPath]);

  return {
    currentPath, items, loading, error, navigateTo, breadcrumbs,
    refresh, fetchList, isActivePath, setError,
  };
}

function buildBreadcrumbs(path: string): { label: string; path: string }[] {
  if (path === '.') return [{ label: '根目录', path: '.' }];
  const crumbs = [{ label: '根目录', path: '.' }];
  let accumulated = '';
  for (const part of path.split('/').filter(Boolean)) {
    accumulated = accumulated ? `${accumulated}/${part}` : part;
    crumbs.push({ label: part, path: accumulated });
  }
  return crumbs;
}
