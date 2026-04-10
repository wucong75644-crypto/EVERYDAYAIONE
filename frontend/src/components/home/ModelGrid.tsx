/**
 * 模型卡片网格
 *
 * "全部" Tab 下按类别分组展示（聊天/图片/视频），单类别 Tab 下平铺。
 * 搜索时不分组，直接平铺结果。
 */

import { Search } from 'lucide-react';
import type { UnifiedModel, ModelType } from '../../constants/models';
import type { TabValue } from './CategoryTabs';
import ModelCard from './ModelCard';
import ModelCardSkeleton from './ModelCardSkeleton';

/** 分组配置 */
const GROUPS: { type: ModelType; icon: string; label: string }[] = [
  { type: 'chat', icon: '💬', label: '聊天模型' },
  { type: 'image', icon: '🎨', label: '图片模型' },
  { type: 'video', icon: '🎬', label: '视频模型' },
];

interface ModelGridProps {
  models: UnifiedModel[];
  activeTab: TabValue;
  searchQuery: string;
  isLoading: boolean;
  isAuthenticated: boolean;
  isSubscribed: (id: string) => boolean;
  isSubscribing: (id: string) => boolean;
  onCardClick: (model: UnifiedModel) => void;
  onSubscribe: (modelId: string) => void;
}

export default function ModelGrid({
  models,
  activeTab,
  searchQuery,
  isLoading,
  isAuthenticated,
  isSubscribed,
  isSubscribing,
  onCardClick,
  onSubscribe,
}: ModelGridProps) {
  if (isLoading) {
    return (
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
          {Array.from({ length: 8 }).map((_, i) => (
            <ModelCardSkeleton key={i} />
          ))}
        </div>
      </div>
    );
  }

  if (models.length === 0) {
    return (
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-20 text-center">
        <Search className="mx-auto w-12 h-12 text-text-disabled" />
        <p className="text-text-tertiary mt-4">未找到匹配的模型</p>
        <p className="text-text-disabled text-sm mt-1">试试其他关键词</p>
      </div>
    );
  }

  const renderCard = (model: UnifiedModel) => (
    <ModelCard
      key={model.id}
      model={model}
      isAuthenticated={isAuthenticated}
      isSubscribed={isSubscribed(model.id)}
      isSubscribing={isSubscribing(model.id)}
      onCardClick={onCardClick}
      onSubscribe={onSubscribe}
    />
  );

  const gridClass = 'grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4';

  // 搜索模式或单类别 Tab：平铺展示
  if (searchQuery || activeTab !== 'all') {
    return (
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
        <div className={gridClass}>{models.map(renderCard)}</div>
      </div>
    );
  }

  // "全部" Tab：按类别分组
  return (
    <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
      {GROUPS.map((group, idx) => {
        const groupModels = models.filter((m) => m.type === group.type);
        if (groupModels.length === 0) return null;
        return (
          <section key={group.type} className={idx === 0 ? '' : 'mt-8'}>
            <h2 className="text-lg font-semibold text-text-primary mb-4">
              {group.icon} {group.label} ({groupModels.length})
            </h2>
            <div className={gridClass}>{groupModels.map(renderCard)}</div>
          </section>
        );
      })}
    </div>
  );
}
