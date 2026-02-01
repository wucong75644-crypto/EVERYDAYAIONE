import { useEffect, useState, type ReactNode } from 'react';

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
  const [isAnimating, setIsAnimating] = useState(false);
  const [shouldRender, setShouldRender] = useState(isOpen);

  // 控制渲染和动画
  useEffect(() => {
    if (isOpen) {
      setShouldRender(true);
      // 等待 DOM 渲染后触发进入动画
      const timer = setTimeout(() => {
        setIsAnimating(true);
      }, 10); // 短暂延迟确保 DOM 已渲染
      return () => clearTimeout(timer);
    } else {
      // 触发退出动画
      setIsAnimating(false);
      // 等待动画结束后卸载
      const timer = setTimeout(() => {
        setShouldRender(false);
      }, 200); // 与动画时长匹配
      return () => clearTimeout(timer);
    }
  }, [isOpen]);

  // ESC 键关闭
  useEffect(() => {
    if (!isOpen || !closeOnEsc) return;

    const handleEsc = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        onClose();
      }
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
        className={`absolute inset-0 bg-black/50 backdrop-blur-sm transition-opacity duration-200 ${
          isAnimating ? 'opacity-100' : 'opacity-0'
        }`}
        onClick={closeOnOverlay ? onClose : undefined}
        aria-hidden="true"
      />

      {/* 弹窗内容 */}
      <div
        className={`relative bg-white rounded-xl shadow-2xl ${maxWidth} w-full mx-4 transition-all duration-200 ${
          isAnimating ? 'opacity-100 translate-y-0' : 'opacity-0 translate-y-4'
        }`}
        role="dialog"
        aria-modal="true"
        aria-labelledby={title ? 'modal-title' : undefined}
      >
        {/* 头部 */}
        {(title || showCloseButton) && (
          <div className="flex items-center justify-between px-5 py-3.5 border-b border-gray-200">
            {title && (
              <h2
                id="modal-title"
                className="text-lg font-semibold text-gray-900"
              >
                {title}
              </h2>
            )}
            {showCloseButton && (
              <button
                onClick={onClose}
                className="text-gray-400 hover:text-gray-600 transition-colors p-1 rounded-lg hover:bg-gray-100"
                aria-label="关闭"
              >
                <svg
                  className="w-5 h-5"
                  fill="none"
                  stroke="currentColor"
                  viewBox="0 0 24 24"
                >
                  <path
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    strokeWidth={2}
                    d="M6 18L18 6M6 6l12 12"
                  />
                </svg>
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
