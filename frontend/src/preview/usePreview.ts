/**
 * usePreview — 统一的预览状态机 Hook
 *
 * 取代调用方各自维护的多个预览 state：
 *   - WorkspaceView 原本有 previewFile / previewImageIndex / previewVideoIndex 三个 state
 *   - FileCard、ChatAttachmentPreview、MessageItem 各自有 previewFile / previewIndex
 *
 * 现在都收敛到一个 hook。
 */

import { useCallback, useState } from 'react';
import type { PreviewItem, PreviewState } from './types';

export interface UsePreviewReturn {
  state: PreviewState;
  /** 打开预览。传单个 item = 单文件场景；传数组+index = 多项可上下张场景 */
  open: (items: PreviewItem | PreviewItem[], index?: number) => void;
  /** 关闭 */
  close: () => void;
  /** 切换索引（上下张回调用） */
  setIndex: (i: number) => void;
  /** 当前是否打开 */
  isOpen: boolean;
}

export function usePreview(): UsePreviewReturn {
  const [state, setState] = useState<PreviewState>({ kind: 'closed' });

  const open = useCallback((items: PreviewItem | PreviewItem[], index = 0) => {
    const arr = Array.isArray(items) ? items : [items];
    if (arr.length === 0) return;
    const safeIndex = Math.max(0, Math.min(index, arr.length - 1));
    setState({ kind: 'open', items: arr, index: safeIndex });
  }, []);

  const close = useCallback(() => setState({ kind: 'closed' }), []);

  const setIndex = useCallback((i: number) => {
    setState((s) => {
      if (s.kind !== 'open') return s;
      if (i < 0 || i >= s.items.length) return s;
      return { ...s, index: i };
    });
  }, []);

  return {
    state,
    open,
    close,
    setIndex,
    isOpen: state.kind === 'open',
  };
}
