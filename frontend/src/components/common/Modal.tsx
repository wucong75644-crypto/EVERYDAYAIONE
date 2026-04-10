/**
 * 通用模态框组件
 *
 * 改造点（V2.0 - 设计系统重构）：
 * - 颜色全部用 token 变量（bg-surface-card / text-text-primary / border-border-default）
 * - 动画用 animations.css 的新 class（modal-enter/exit + backdrop-enter/exit）
 * - z-index 用标准数字（z-50）
 * - 关闭按钮换 lucide-react 图标（移除内联 SVG）
 * - 跟随主题切换（classic / claude）
 *
 * API 完全兼容旧版（isOpen/onClose/title/children/closeOnOverlay/closeOnEsc/showCloseButton/maxWidth）
 */

import { useEffect, type ReactNode } from 'react';
import { X } from 'lucide-react';
import { cn } from '../../utils/cn';
import { useExitAnimation } from '../../hooks/useExitAnimation';

interface ModalProps {
  isOpen: boolean;
  onClose: () => void;
  title?: string;
  children: ReactNode;
  /** 是否允许点击遮罩层关闭 */
  closeOnOverlay?: boolean;
  /** 是否允许按 ESC 键关闭 */
  closeOnEsc?: boolean;
  /** 是否显示关闭按钮 */
  showCloseButton?: boolean;
  /** 自定义宽度（默认 max-w-md） */
  maxWidth?: string;
}

/** 退出动画时长，与 animations.css 的 modal-exit (--duration-normal = 150ms) 一致 */
const EXIT_ANIMATION_DURATION = 150;

export default function Modal({
  isOpen,
  onClose,
  title,
  children,
  closeOnOverlay = true,
  closeOnEsc = true,
  showCloseButton = true,
  maxWidth = 'max-w-md',
}: ModalProps) {
  // 退出动画状态机（统一复用 useExitAnimation Hook）
  const { shouldRender, isClosing } = useExitAnimation(isOpen, EXIT_ANIMATION_DURATION);

  // ESC 键关闭
  useEffect(() => {
    if (!isOpen || !closeOnEsc) return;

    const handleEsc = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };

    document.addEventListener('keydown', handleEsc);
    return () => document.removeEventListener('keydown', handleEsc);
  }, [isOpen, closeOnEsc, onClose]);

  // 防止背景滚动
  useEffect(() => {
    if (isOpen) {
      document.body.style.overflow = 'hidden';
    } else {
      document.body.style.overflow = '';
    }

    return () => {
      document.body.style.overflow = '';
    };
  }, [isOpen]);

  if (!shouldRender) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      {/* 遮罩层 */}
      <div
        className={cn(
          'absolute inset-0 bg-black/50 backdrop-blur-sm',
          isClosing ? 'animate-backdrop-exit' : 'animate-backdrop-enter',
        )}
        onClick={closeOnOverlay ? onClose : undefined}
        aria-hidden="true"
      />

      {/* 弹窗内容 */}
      <div
        className={cn(
          'relative bg-surface-card text-text-primary',
          'rounded-xl shadow-xl border border-border-light',
          maxWidth,
          'w-full mx-4',
          isClosing ? 'animate-modal-exit' : 'animate-modal-enter',
        )}
        role="dialog"
        aria-modal="true"
        aria-labelledby={title ? 'modal-title' : undefined}
      >
        {/* 头部 */}
        {(title || showCloseButton) && (
          <div className="flex items-center justify-between px-5 py-3.5 border-b border-border-default">
            {title && (
              <h2
                id="modal-title"
                className="text-lg font-semibold text-text-primary"
              >
                {title}
              </h2>
            )}
            {showCloseButton && (
              <button
                onClick={onClose}
                className={cn(
                  'text-text-tertiary hover:text-text-primary hover:bg-hover',
                  'p-1 rounded-lg transition-base',
                  'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus-ring',
                )}
                aria-label="关闭"
              >
                <X className="w-5 h-5" />
              </button>
            )}
          </div>
        )}

        {/* 内容区域 */}
        <div className="p-5">{children}</div>
      </div>
    </div>
  );
}
