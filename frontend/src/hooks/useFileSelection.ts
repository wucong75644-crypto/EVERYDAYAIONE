/**
 * 文件多选状态管理 Hook
 *
 * 支持：单选、Ctrl/Cmd 多选、Shift 范围选、全选、清空。
 * 选中集合用 Set<string>（存 workspace 相对路径）。
 */

import { useState, useCallback, useRef } from 'react';

export interface UseFileSelectionReturn {
  /** 选中的文件路径集合 */
  selectedPaths: Set<string>;
  /** 选中数量 */
  selectedCount: number;
  /** 是否有选中 */
  hasSelection: boolean;
  /** 单击选中（替换选中） */
  select: (path: string) => void;
  /** Ctrl/Cmd 切换选中 */
  toggle: (path: string) => void;
  /** Shift 范围选中（基于有序 items 列表） */
  selectRange: (path: string, orderedPaths: string[]) => void;
  /** 全选 */
  selectAll: (paths: string[]) => void;
  /** 清空选中 */
  clear: () => void;
  /** 判断单个路径是否选中 */
  isSelected: (path: string) => boolean;
  /** 处理点击事件（自动判断 Ctrl/Shift） */
  handleClick: (path: string, orderedPaths: string[], e: { ctrlKey: boolean; metaKey: boolean; shiftKey: boolean }) => void;
}

export function useFileSelection(): UseFileSelectionReturn {
  const [selectedPaths, setSelectedPaths] = useState<Set<string>>(new Set());
  // 记录最后一次单击的路径（Shift 范围选的锚点）
  const lastClickedRef = useRef<string | null>(null);

  const select = useCallback((path: string) => {
    setSelectedPaths(new Set([path]));
    lastClickedRef.current = path;
  }, []);

  const toggle = useCallback((path: string) => {
    setSelectedPaths((prev) => {
      const next = new Set(prev);
      if (next.has(path)) {
        next.delete(path);
      } else {
        next.add(path);
      }
      return next;
    });
    lastClickedRef.current = path;
  }, []);

  const selectRange = useCallback((path: string, orderedPaths: string[]) => {
    const anchor = lastClickedRef.current;
    if (!anchor) {
      // 没有锚点，退化为单选
      setSelectedPaths(new Set([path]));
      lastClickedRef.current = path;
      return;
    }

    const anchorIdx = orderedPaths.indexOf(anchor);
    const targetIdx = orderedPaths.indexOf(path);
    if (anchorIdx === -1 || targetIdx === -1) {
      setSelectedPaths(new Set([path]));
      lastClickedRef.current = path;
      return;
    }

    const start = Math.min(anchorIdx, targetIdx);
    const end = Math.max(anchorIdx, targetIdx);
    const range = orderedPaths.slice(start, end + 1);
    setSelectedPaths(new Set(range));
    // 注意：Shift 选不更新锚点（和 Finder/Explorer 行为一致）
  }, []);

  const selectAll = useCallback((paths: string[]) => {
    setSelectedPaths(new Set(paths));
  }, []);

  const clear = useCallback(() => {
    setSelectedPaths(new Set());
    lastClickedRef.current = null;
  }, []);

  const isSelected = useCallback((path: string) => {
    return selectedPaths.has(path);
  }, [selectedPaths]);

  const handleClick = useCallback((
    path: string,
    orderedPaths: string[],
    e: { ctrlKey: boolean; metaKey: boolean; shiftKey: boolean },
  ) => {
    if (e.shiftKey) {
      selectRange(path, orderedPaths);
    } else if (e.ctrlKey || e.metaKey) {
      toggle(path);
    } else {
      select(path);
    }
  }, [select, toggle, selectRange]);

  return {
    selectedPaths,
    selectedCount: selectedPaths.size,
    hasSelection: selectedPaths.size > 0,
    select,
    toggle,
    selectRange,
    selectAll,
    clear,
    isSelected,
    handleClick,
  };
}
