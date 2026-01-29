/**
 * 模型选择器组件
 *
 * 显示当前选中的模型，并提供下拉菜单选择其他可用模型
 */

import { useState, useRef, useEffect } from 'react';
import { type UnifiedModel } from '../../constants/models';
import { MODAL_CLOSE_ANIMATION_DURATION } from '../../constants/animations';

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
  const [showDropdown, setShowDropdown] = useState(false);
  const [dropdownClosing, setDropdownClosing] = useState(false);
  const selectorRef = useRef<HTMLDivElement>(null);

  // 关闭下拉框（带动画）
  const closeDropdown = () => {
    setDropdownClosing(true);
    setTimeout(() => {
      setShowDropdown(false);
      setDropdownClosing(false);
    }, MODAL_CLOSE_ANIMATION_DURATION);
  };

  // 点击外部关闭下拉框
  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (selectorRef.current && !selectorRef.current.contains(e.target as Node)) {
        if (showDropdown) {
          closeDropdown();
        }
      }
    };
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, [showDropdown]);

  /**
   * 渲染模型图标
   */
  const renderModelIcon = (model: UnifiedModel, className: string = 'w-4 h-4 text-gray-700') => {
    if (model.type === 'chat') {
      return (
        <svg className={className} fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            strokeWidth={2}
            d="M8 10h.01M12 10h.01M16 10h.01M9 16H5a2 2 0 01-2-2V6a2 2 0 012-2h14a2 2 0 012 2v8a2 2 0 01-2 2h-5l-5 5v-5z"
          />
        </svg>
      );
    } else if (model.capabilities.imageEditing) {
      return (
        <svg className={className} fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            strokeWidth={2}
            d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z"
          />
        </svg>
      );
    } else {
      return (
        <svg className={className} fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            strokeWidth={2}
            d="M4 16l4.586-4.586a2 2 0 012.828 0L16 16m-2-2l1.586-1.586a2 2 0 012.828 0L20 14m-6-6h.01M6 20h12a2 2 0 002-2V6a2 2 0 00-2-2H6a2 2 0 00-2 2v12a2 2 0 002 2z"
          />
        </svg>
      );
    }
  };

  return (
    <div className="relative" ref={selectorRef}>
      <button
        onClick={() => {
          if (!locked && !loading) {
            setShowDropdown(!showDropdown);
          }
        }}
        disabled={locked || loading}
        className={`flex items-center space-x-2 px-3 py-1.5 rounded-lg transition-colors ${
          locked || loading ? 'opacity-60 cursor-not-allowed bg-gray-50' : 'hover:bg-gray-100'
        }`}
        title={loading ? '加载中...' : lockTooltip}
      >
        {/* 加载指示器（加载中显示） */}
        {loading && (
          <svg className="w-4 h-4 text-gray-500 animate-spin" fill="none" viewBox="0 0 24 24">
            <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
            <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
          </svg>
        )}

        {/* 锁定图标（锁定且非加载时显示） */}
        {locked && !loading && (
          <svg className="w-3 h-3 text-gray-500" fill="currentColor" viewBox="0 0 20 20">
            <path
              fillRule="evenodd"
              d="M5 9V7a5 5 0 0110 0v2a2 2 0 012 2v5a2 2 0 01-2 2H5a2 2 0 01-2-2v-5a2 2 0 012-2zm8-2v2H7V7a3 3 0 016 0z"
              clipRule="evenodd"
            />
          </svg>
        )}

        {/* 模型图标 */}
        {renderModelIcon(selectedModel)}

        <span className="text-sm font-medium text-gray-700">{selectedModel.name}</span>

        {/* 下拉箭头（非锁定非加载时显示） */}
        {!locked && !loading && (
          <svg className="w-4 h-4 text-gray-500" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
          </svg>
        )}
      </button>

      {/* 下拉菜单 */}
      {showDropdown && (
        <div
          className={`absolute bottom-full mb-2 left-0 w-64 bg-white rounded-lg shadow-lg border border-gray-200 py-2 z-10 max-h-80 overflow-y-auto ${
            dropdownClosing ? 'animate-popupExit' : 'animate-popupEnter'
          }`}
        >
          {availableModels.map((model) => (
            <button
              key={model.id}
              onClick={() => {
                onSelectModel(model);
                closeDropdown();
              }}
              className={`w-full px-4 py-2.5 text-left hover:bg-gray-50 transition-colors flex items-start space-x-3 ${
                selectedModel.id === model.id ? 'bg-gray-100' : ''
              }`}
            >
              {/* 图标 */}
              <div className="flex-shrink-0 mt-0.5">
                {renderModelIcon(model, 'w-5 h-5 text-gray-600')}
              </div>

              {/* 文字信息 */}
              <div className="flex-1 min-w-0">
                <div className="font-medium text-sm text-gray-900">{model.name}</div>
                <div className="text-xs text-gray-500 mt-0.5">{model.description}</div>
              </div>

              {/* 选中标记 */}
              {selectedModel.id === model.id && (
                <svg className="w-5 h-5 text-blue-600 flex-shrink-0" fill="currentColor" viewBox="0 0 20 20">
                  <path
                    fillRule="evenodd"
                    d="M16.707 5.293a1 1 0 010 1.414l-8 8a1 1 0 01-1.414 0l-4-4a1 1 0 011.414-1.414L8 12.586l7.293-7.293a1 1 0 011.414 0z"
                    clipRule="evenodd"
                  />
                </svg>
              )}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
