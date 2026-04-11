/**
 * 分类标签（V3 — layoutId Magic Move 指示条）
 *
 * V3 升级：
 * - 底部指示条用 layoutId 共享层，切换 tab 时指示条从一个滑到另一个（苹果级丝滑）
 * - 保留 sticky 顶栏位置
 */

import { m, LayoutGroup } from 'framer-motion';
import type { ModelType } from '../../constants/models';
import { SOFT_SPRING } from '../../utils/motion';

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
        <LayoutGroup id="category-tabs">
          <div className="flex space-x-1 overflow-x-auto">
            {TABS.map((tab) => {
              const isActive = activeTab === tab.value;
              return (
                <button
                  key={tab.value}
                  onClick={() => onTabChange(tab.value)}
                  className={`relative px-4 py-2.5 text-sm font-medium whitespace-nowrap transition-colors ${
                    isActive
                      ? 'text-accent'
                      : 'text-text-tertiary hover:text-text-secondary'
                  }`}
                >
                  {tab.icon && `${tab.icon} `}
                  {tab.label}
                  {' '}
                  <span className="text-xs text-text-disabled">
                    ({counts[tab.value] ?? 0})
                  </span>

                  {/* 底部指示条 — Magic Move layoutId */}
                  {isActive && (
                    <m.div
                      layoutId="category-tab-indicator"
                      className="absolute left-0 right-0 bottom-0 h-0.5 bg-accent"
                      transition={SOFT_SPRING}
                    />
                  )}
                </button>
              );
            })}
          </div>
        </LayoutGroup>
      </div>
    </div>
  );
}
