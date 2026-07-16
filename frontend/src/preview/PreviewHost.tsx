/**
 * PreviewHost — 预览唯一入口组件
 *
 * 调用方只需渲染 `<PreviewHost state={preview.state} onClose={preview.close} onIndexChange={preview.setIndex} />`，
 * 不用关心是哪个 Modal/adapter 在工作 —— 由 registry 路由。
 */

import { memo } from 'react';
import type { PreviewState } from './types';
import { resolveAdapter } from './registry';

interface PreviewHostProps {
  state: PreviewState;
  onClose: () => void;
  /** 切换索引（图片/视频上下张）*/
  onIndexChange: (i: number) => void;
  /** 删除当前预览项（仅聊天输入框的附件预览场景使用）*/
  onDelete?: () => void;
}

export default memo(function PreviewHost({
  state,
  onClose,
  onIndexChange,
  onDelete,
}: PreviewHostProps) {
  if (state.kind !== 'open') return null;

  const { items, index } = state;
  const current = items[index];
  if (!current) return null;

  const adapter = resolveAdapter(current);
  if (!adapter) {
    // registry 应该至少包含 fallbackAdapter（priority=0, always-match），
    // 走到这里说明 registry 还未加载 adapter（Phase 1 框架初始化阶段）
    if (import.meta.env.DEV) {
      // eslint-disable-next-line no-console
      console.warn('[PreviewHost] no adapter resolved; registry empty?', current);
    }
    return null;
  }

  const { Component } = adapter;
  return (
    <Component
      item={current}
      siblings={items}
      index={index}
      onClose={onClose}
      onNavigate={onIndexChange}
      onDelete={onDelete}
    />
  );
});
