/**
 * 分类标签
 *
 * 全部 / 聊天 / 图片 / 视频
 */

import type { ModelType } from '../../constants/models';

export type TabValue = 'all' | ModelType;

interface Tab {
  value: TabValue;
  label: string;
  icon: string;
}

const TABS: Tab[] = [
  { value: 'all', label: '全部', icon: '' },
  { value: 'chat', label: '聊天', icon: '💬' },
  { value: 'image', label: '图片', icon: '🎨' },
  { value: 'video', label: '视频', icon: '🎬' },
];

interface CategoryTabsProps {
  activeTab: TabValue;
  onTabChange: (tab: TabValue) => void;
  counts: Record<TabValue, number>;
}

export default function CategoryTabs({
  activeTab,
  onTabChange,
  counts,
}: CategoryTabsProps) {
  return (
    <div className="border-b border-border-default sticky top-16 bg-surface-card z-10">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
        <div className="flex space-x-1 overflow-x-auto">
          {TABS.map((tab) => (
            <button
              key={tab.value}
              onClick={() => onTabChange(tab.value)}
              className={`px-4 py-2.5 text-sm font-medium whitespace-nowrap transition-base ${
                activeTab === tab.value
                  ? 'text-accent border-b-2 border-accent'
                  : 'text-text-tertiary hover:text-text-secondary'
              }`}
            >
              {tab.icon && `${tab.icon} `}
              {tab.label}
              {' '}
              <span className="text-xs text-text-disabled">
                ({counts[tab.value] ?? 0})
              </span>
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
