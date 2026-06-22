/**
 * 工作区分类筛选 Tab
 *
 * 单独一行，紧贴 Header 下方。蓝色下划线指示选中态。
 * 三类：全部 / 文档 / 图片与视频。
 * 右侧：「多选」toggle + 「下载 (N)」条件渲染（多选模式 + 有选中时显示）。
 */

import { CheckSquare, Square, Download } from 'lucide-react';
import { cn } from '../../utils/cn';
import type { CategoryFilter } from '../../utils/fileCategory';

interface TabDef {
  key: CategoryFilter;
  label: string;
}

const TABS: TabDef[] = [
  { key: 'all', label: '全部' },
  { key: 'documents', label: '文档' },
  { key: 'images', label: '图片与视频' },
];

interface WorkspaceCategoryTabsProps {
  value: CategoryFilter;
  onChange: (filter: CategoryFilter) => void;
  multiSelectMode: boolean;
  onToggleMultiSelect: () => void;
  selectedCount: number;
  onBatchDownload: () => void;
}

export default function WorkspaceCategoryTabs({
  value,
  onChange,
  multiSelectMode,
  onToggleMultiSelect,
  selectedCount,
  onBatchDownload,
}: WorkspaceCategoryTabsProps) {
  return (
    <div
      className="flex items-center px-4 border-b border-[var(--s-border-default)] bg-[var(--s-surface-overlay)] shrink-0"
    >
      {/* Tab 列表 */}
      <div role="tablist" aria-label="文件分类" className="flex items-center gap-1 flex-1 min-w-0">
        {TABS.map((tab) => {
          const active = value === tab.key;
          return (
            <button
              key={tab.key}
              type="button"
              role="tab"
              aria-selected={active}
              onClick={() => onChange(tab.key)}
              className={cn(
                'relative px-3 py-2.5 text-sm transition-colors',
                'outline-none focus-visible:ring-2 focus-visible:ring-[var(--s-border-focus)] rounded-t',
                active
                  ? 'text-[var(--s-text-primary)] font-medium'
                  : 'text-[var(--s-text-secondary)] hover:text-[var(--s-text-primary)]',
              )}
            >
              {tab.label}
              {active && (
                <span
                  aria-hidden
                  className="absolute left-3 right-3 -bottom-px h-0.5 bg-[var(--s-accent)] rounded-full"
                />
              )}
            </button>
          );
        })}
      </div>

      {/* 右侧：多选模式 + 下载按钮 */}
      <div className="flex items-center gap-1 shrink-0">
        {/* 多选模式 + 有选中 → 显示「下载 (N)」 */}
        {multiSelectMode && selectedCount > 0 && (
          <button
            type="button"
            onClick={onBatchDownload}
            className={cn(
              'flex items-center gap-1.5 px-3 py-1.5 text-sm rounded-[var(--s-radius-control)]',
              'bg-[var(--s-accent)] text-white hover:bg-[var(--s-accent-hover)] transition-colors',
              'outline-none focus-visible:ring-2 focus-visible:ring-[var(--s-border-focus)]',
            )}
            aria-label={`下载选中的 ${selectedCount} 项`}
          >
            <Download className="w-4 h-4" />
            <span>下载 ({selectedCount})</span>
          </button>
        )}

        {/* 多选按钮（toggle） */}
        <button
          type="button"
          onClick={onToggleMultiSelect}
          className={cn(
            'flex items-center gap-1.5 px-3 py-1.5 text-sm rounded-[var(--s-radius-control)] transition-colors',
            'outline-none focus-visible:ring-2 focus-visible:ring-[var(--s-border-focus)]',
            multiSelectMode
              ? 'bg-[var(--s-accent-soft)] text-[var(--s-accent)]'
              : 'text-[var(--s-text-secondary)] hover:text-[var(--s-text-primary)] hover:bg-[var(--s-hover)]',
          )}
          aria-pressed={multiSelectMode}
          title={multiSelectMode ? '退出多选' : '多选'}
        >
          {multiSelectMode ? <CheckSquare className="w-4 h-4" /> : <Square className="w-4 h-4" />}
          <span>{multiSelectMode ? '退出多选' : '多选'}</span>
        </button>
      </div>
    </div>
  );
}
