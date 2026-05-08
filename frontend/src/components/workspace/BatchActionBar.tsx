/**
 * 批量操作工具栏
 *
 * 选中 ≥1 个文件时显示在 Header 下方。
 */

import { Trash2, X } from 'lucide-react';
import { Button } from '../ui/Button';

interface BatchActionBarProps {
  selectedCount: number;
  onDelete: () => void;
  onClear: () => void;
}

export default function BatchActionBar({ selectedCount, onDelete, onClear }: BatchActionBarProps) {
  if (selectedCount === 0) return null;

  return (
    <div className="flex items-center gap-3 px-4 py-2 border-b border-[var(--s-border-default)] bg-[var(--s-accent)]/5 shrink-0">
      <span className="text-sm text-[var(--s-text-secondary)]">
        已选中 <span className="font-medium text-[var(--s-text-primary)]">{selectedCount}</span> 个文件
      </span>
      <div className="flex-1" />
      <Button variant="danger" size="sm" onClick={onDelete}>
        <Trash2 className="w-3.5 h-3.5" />
        <span>删除</span>
      </Button>
      <Button variant="ghost" size="sm" onClick={onClear}>
        <X className="w-3.5 h-3.5" />
        <span>取消选择</span>
      </Button>
    </div>
  );
}
