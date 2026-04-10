/**
 * 模型选择器组件
 *
 * 显示当前选中的模型，并提供下拉菜单选择其他可用模型
 *
 * 改造（V2 - 设计系统重构）：
 * - 全 token 化（跟随主题）
 * - 8+ 处内联 SVG → lucide-react
 * - 改用 useExitAnimation 统一退出动画状态机
 */

import { useState, useRef, useEffect } from 'react';
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
import { useExitAnimation } from '../../../hooks/useExitAnimation';

/** 退出动画时长（与 popup-exit 一致 = --duration-fast 100ms） */
const EXIT_DURATION = 100;

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

  // 退出动画状态机
  const { shouldRender, isClosing } = useExitAnimation(isOpen, EXIT_DURATION);

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

      {/* 下拉菜单 */}
      {shouldRender && (
        <div
          className={`absolute bottom-full mb-2 left-0 w-64 bg-surface-card rounded-lg shadow-lg border border-border-default py-2 z-30 max-h-80 overflow-y-auto ${
            isClosing ? 'animate-popup-exit' : 'animate-popup-enter'
          }`}
        >
          {availableModels.map((model) => (
            <button
              key={model.id}
              onClick={() => {
                onSelectModel(model);
                setIsOpen(false);
              }}
              className={`w-full px-4 py-2.5 text-left hover:bg-hover transition-base flex items-start space-x-3 ${
                selectedModel.id === model.id ? 'bg-hover' : ''
              }`}
            >
              {/* 图标 */}
              <div className="flex-shrink-0 mt-0.5">
                {renderModelIcon(model, 'w-5 h-5 text-text-tertiary')}
              </div>

              {/* 文字信息 */}
              <div className="flex-1 min-w-0">
                <div className="font-medium text-sm text-text-primary">{model.name}</div>
                <div className="text-xs text-text-tertiary mt-0.5">{model.description}</div>
              </div>

              {/* 选中标记 */}
              {selectedModel.id === model.id && (
                <Check className="w-5 h-5 text-accent flex-shrink-0" />
              )}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
