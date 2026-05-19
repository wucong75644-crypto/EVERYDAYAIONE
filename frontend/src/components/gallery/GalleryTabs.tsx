/**
 * 画廊分类标签栏
 *
 * 复用首页 CategoryTabs 的 Magic Move 指示条风格。
 */

import { m, LayoutGroup } from 'framer-motion';
import {
  Camera,
  LayoutGrid,
  Megaphone,
  UtensilsCrossed,
  Palette,
  Video,
} from 'lucide-react';
import { SOFT_SPRING } from '../../utils/motion';
import type { PromptCategory } from './types';

const ICON_MAP: Record<string, React.ElementType> = {
  camera: Camera,
  layout: LayoutGrid,
  megaphone: Megaphone,
  utensils: UtensilsCrossed,
  palette: Palette,
  video: Video,
};

interface GalleryTabsProps {
  categories: PromptCategory[];
  activeCategory: string;
  onCategoryChange: (id: string) => void;
  totalCount: number;
}

export default function GalleryTabs({
  categories,
  activeCategory,
  onCategoryChange,
  totalCount,
}: GalleryTabsProps) {
  const tabs = [
    { id: 'all', name: '全部', count: totalCount },
    ...categories.map((c) => ({ id: c.id, name: c.name, count: c.count, icon: c.icon })),
  ];

  return (
    <div className="border-b border-border-default sticky top-16 bg-surface-card z-10">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
        <LayoutGroup id="gallery-tabs">
          <div className="flex space-x-1 overflow-x-auto scrollbar-hide">
            {tabs.map((tab) => {
              const isActive = activeCategory === tab.id;
              const Icon = 'icon' in tab ? ICON_MAP[tab.icon as string] : undefined;
              return (
                <button
                  key={tab.id}
                  onClick={() => onCategoryChange(tab.id)}
                  className={`relative px-4 py-2.5 text-sm font-medium whitespace-nowrap transition-colors flex items-center gap-1.5 ${
                    isActive
                      ? 'text-accent'
                      : 'text-text-tertiary hover:text-text-secondary'
                  }`}
                >
                  {Icon && <Icon className="w-3.5 h-3.5" />}
                  {tab.name}
                  {' '}
                  <span className="text-xs text-text-disabled">
                    ({tab.count})
                  </span>

                  {isActive && (
                    <m.div
                      layoutId="gallery-tab-indicator"
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
