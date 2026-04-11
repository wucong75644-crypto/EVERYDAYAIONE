/**
 * 通用模态框组件（V3 — Radix Dialog 底座 + framer motion）
 *
 * V3 重大升级（架构隐患 2 修复）：
 * - 内部实现从自研 useExitAnimation 换成 primitives/Dialog（基于 Radix UI Dialog）
 * - Portal 渲染到 body（不再受父级 z-index 影响）
 * - 完整 a11y：焦点 trap / 键盘 ESC / 锁滚动 / aria-modal 全部由 Radix 处理
 * - 进出场改为 framer spring scale + fade（丝滑感）
 * - 外部 API 完全保留：isOpen / onClose / title / closeOnOverlay / closeOnEsc
 *   / showCloseButton / maxWidth，6 个 Modal 使用者零修改
 *
 * V3 Review Fix：
 * - closeOnOverlay/closeOnEsc 真正透传到底层 primitives/Dialog
 *   （旧版 silently no-op 是 review 发现的 HIGH bug）
 *
 * @example
 * ```tsx
 * <Modal isOpen={open} onClose={() => setOpen(false)} title="标题">
 *   <p>内容</p>
 * </Modal>
 * ```
 */

import { type ReactNode } from 'react';
import { Dialog } from '../primitives/Dialog';

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
  /** 自定义宽度 Tailwind class（如 'max-w-md' / 'max-w-2xl'） */
  maxWidth?: string;
}

/**
 * maxWidth 字符串到 primitives/Dialog size 的映射。
 * 对于不能映射的自定义值，传入 className。
 */
function mapMaxWidthToSize(
  maxWidth: string,
): { size: 'sm' | 'md' | 'lg' | 'xl' | 'full'; className?: string } {
  switch (maxWidth) {
    case 'max-w-sm':
      return { size: 'sm' };
    case 'max-w-md':
      return { size: 'md' };
    case 'max-w-2xl':
      return { size: 'lg' };
    case 'max-w-4xl':
      return { size: 'xl' };
    default:
      // 自定义值用 className 覆盖，size 用 md 做基础
      return { size: 'md', className: maxWidth };
  }
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
  const { size, className: sizeClass } = mapMaxWidthToSize(maxWidth);

  // Radix 在 ESC / backdrop click / close button 触发 open=false 时映射到 onClose
  // 当 closeOnEsc/closeOnOverlay=false 时，primitives/Dialog 的
  // onEscapeKeyDown / onPointerDownOutside / onInteractOutside 会 preventDefault
  // 阻止 onOpenChange 触发，这里只需要透传 prop
  const handleOpenChange = (nextOpen: boolean) => {
    if (!nextOpen) onClose();
  };

  return (
    <Dialog
      open={isOpen}
      onOpenChange={handleOpenChange}
      title={title}
      hideTitleVisually={true}
      size={size}
      padding="none"
      showClose={showCloseButton}
      closeOnEscape={closeOnEsc}
      closeOnOutsideClick={closeOnOverlay}
      className={sizeClass}
    >
      {/* 头部（含显示的 h2 标题 + 分隔线）
          a11y 的 Dialog.Title 已由 primitives/Dialog 用 sr-only 渲染，此处纯视觉 */}
      {title && (
        <div className="px-5 pt-5 pb-3.5 border-b border-[var(--s-border-default)]">
          <h2
            className="text-lg font-semibold text-[var(--s-text-primary)] pr-8"
            style={{ fontFamily: 'var(--s-font-heading)' }}
            aria-hidden="true"
          >
            {title}
          </h2>
        </div>
      )}

      {/* 内容区域（保持旧 Modal 的 p-5 padding） */}
      <div className="p-5">{children}</div>
    </Dialog>
  );
}
