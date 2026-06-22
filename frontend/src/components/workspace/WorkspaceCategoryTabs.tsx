/**
 * 工作区分类筛选 Tab
 *
 * 单独一行，紧贴 Header 下方。蓝色下划线指示选中态。
 * 三类：全部 / 文档 / 图片与视频。
 */

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
}

export default function WorkspaceCategoryTabs({ value, onChange }: WorkspaceCategoryTabsProps) {
  return (
    <div
      role="tablist"
      aria-label="文件分类"
      className="flex items-center gap-1 px-4 border-b border-[var(--s-border-default)] bg-[var(--s-surface-overlay)] shrink-0"
    >
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
  );
}
