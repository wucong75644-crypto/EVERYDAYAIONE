/**
 * 模型选择器组件（V3 — framer AnimatePresence + layoutId Magic Move）
 *
 * V3 升级：
 * - popup 进出场改用 framer AnimatePresence + spring
 * - 选中项背景用 layoutId="model-selected-bg" 共享层，切换时背景平滑滑动到新 item
 * - 保留 useExitAnimation 移除（被 AnimatePresence 替代）
 */

import { useState, useRef, useEffect } from 'react';
import { AnimatePresence, LayoutGroup, m } from 'framer-motion';
import {
  Sparkles,
  MessageSquare,
  Pencil,
  ImagePlus,
  Loader2,
  Lock,
  ChevronDown,
  Check,
} from 'lucide-react';
import { type UnifiedModel } from '../../../constants/models';
import { isSmartModel } from '../../../constants/smartModel';
import { slideUpVariants, SOFT_SPRING } from '../../../utils/motion';

interface ModelSelectorProps {
  selectedModel: UnifiedModel;
  availableModels: UnifiedModel[];
  locked: boolean;
  lockTooltip: string;
  onSelectModel: (model: UnifiedModel) => void;
  /** 模型加载中状态（切换对话时） */
  loading?: boolean;
}

export default function ModelSelector({
  selectedModel,
  availableModels,
  locked,
  lockTooltip,
  onSelectModel,
  loading = false,
}: ModelSelectorProps) {
  const [isOpen, setIsOpen] = useState(false);
  const selectorRef = useRef<HTMLDivElement>(null);

  // 点击外部关闭下拉框
  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (selectorRef.current && !selectorRef.current.contains(e.target as Node)) {
        if (isOpen) {
          setIsOpen(false);
        }
      }
    };
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, [isOpen]);

  /**
   * 渲染模型图标（用 lucide 图标替代内联 SVG）
   */
  const renderModelIcon = (model: UnifiedModel, className: string = 'w-4 h-4 text-text-secondary') => {
    if (isSmartModel(model.id)) {
      return <Sparkles className={className} />;
    } else if (model.type === 'chat') {
      return <MessageSquare className={className} />;
    } else if (model.capabilities.imageEditing) {
      return <Pencil className={className} />;
    } else {
      return <ImagePlus className={className} />;
    }
  };

  return (
    <div className="relative" ref={selectorRef}>
      <button
        onClick={() => {
          if (!locked && !loading) {
            setIsOpen(!isOpen);
          }
        }}
        disabled={locked || loading}
        className={`flex items-center space-x-2 px-3 py-1.5 rounded-lg transition-base ${
          locked || loading
            ? 'opacity-60 cursor-not-allowed bg-hover'
            : 'hover:bg-hover'
        }`}
        title={loading ? '加载中...' : lockTooltip}
      >
        {/* 加载指示器（加载中显示） */}
        {loading && <Loader2 className="w-4 h-4 text-text-tertiary animate-spin" />}

        {/* 锁定图标（锁定且非加载时显示） */}
        {locked && !loading && <Lock className="w-3 h-3 text-text-tertiary" />}

        {/* 模型图标 */}
        {renderModelIcon(selectedModel)}

        <span className="text-sm font-medium text-text-secondary">{selectedModel.name}</span>

        {/* 下拉箭头（非锁定非加载时显示） */}
        {!locked && !loading && <ChevronDown className="w-4 h-4 text-text-tertiary" />}
      </button>

      {/* 下拉菜单（framer AnimatePresence spring） */}
      <AnimatePresence>
        {isOpen && (
          <m.div
            className="absolute bottom-full mb-2 left-0 w-64 bg-surface-card rounded-lg shadow-lg border border-border-default py-2 z-30 max-h-80 overflow-y-auto"
            variants={slideUpVariants}
            initial="initial"
            animate="animate"
            exit="exit"
          >
            {/* LayoutGroup 包裹，让 layoutId 生效 */}
            <LayoutGroup id="model-selector">
              {availableModels.map((model) => {
                const isSelected = selectedModel.id === model.id;
                return (
                  <button
                    key={model.id}
                    onClick={() => {
                      onSelectModel(model);
                      setIsOpen(false);
                    }}
                    className="relative w-full px-4 py-2.5 text-left hover:bg-hover transition-base flex items-start space-x-3"
                  >
                    {/* 选中项背景层 — Magic Move layoutId */}
                    {isSelected && (
                      <m.div
                        layoutId="model-selected-bg"
                        className="absolute inset-0 bg-accent-light pointer-events-none"
                        transition={SOFT_SPRING}
                      />
                    )}

                    {/* 图标 */}
                    <div className="relative flex-shrink-0 mt-0.5">
                      {renderModelIcon(model, 'w-5 h-5 text-text-tertiary')}
                    </div>

                    {/* 文字信息 */}
                    <div className="relative flex-1 min-w-0">
                      <div className="font-medium text-sm text-text-primary">{model.name}</div>
                      <div className="text-xs text-text-tertiary mt-0.5">{model.description}</div>
                    </div>

                    {/* 选中标记 */}
                    {isSelected && (
                      <Check className="relative w-5 h-5 text-accent flex-shrink-0" />
                    )}
                  </button>
                );
              })}
            </LayoutGroup>
          </m.div>
        )}
      </AnimatePresence>
    </div>
  );
}
