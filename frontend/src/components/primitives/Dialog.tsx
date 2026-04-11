/**
 * Dialog Primitive
 *
 * 基于 Radix UI Dialog + framer-motion 的薄封装。
 * 提供：
 * - 完整 a11y（焦点 trap / ESC 关闭 / aria-modal / 锁滚动）
 * - Portal 渲染到 body（不受父级 z-index 影响）
 * - 进出场动画（spring scale + fade backdrop）
 * - 3 主题 token 自动接入
 * - 毛玻璃 backdrop（可选 frosted variant）
 *
 * 使用方式（受控 API，外层自己管 open state）：
 * ```tsx
 * const [open, setOpen] = useState(false);
 * <Dialog
 *   open={open}
 *   onOpenChange={setOpen}
 *   title="删除确认"
 *   description="此操作无法撤销"
 *   size="md"
 * >
 *   <p>详细内容</p>
 *   <DialogFooter>
 *     <Button onClick={() => setOpen(false)}>取消</Button>
 *     <Button variant="danger" onClick={handleDelete}>删除</Button>
 *   </DialogFooter>
 * </Dialog>
 * ```
 *
 * Phase 4：提供底座。Phase 7 将 common/Modal 内部换成这个，外部 API 保留。
 */

import { forwardRef, type ReactNode } from 'react';
import * as RadixDialog from '@radix-ui/react-dialog';
import { AnimatePresence, m } from 'framer-motion';
import { X } from 'lucide-react';
import { cn } from '../../utils/cn';
import { scaleVariants, fadeVariants } from '../../utils/motion';

export type DialogSize = 'sm' | 'md' | 'lg' | 'xl' | 'full';
export type DialogBackdrop = 'dim' | 'glass';

export type DialogPadding = 'default' | 'none';

interface DialogProps {
  /** 受控打开状态 */
  open: boolean;
  /** 打开状态变更（ESC / backdrop 点击 / close 按钮） */
  onOpenChange: (open: boolean) => void;
  /** 标题（用于 a11y，同时渲染 h2） */
  title?: ReactNode;
  /** 描述文字（aria-description） */
  description?: ReactNode;
  /**
   * 视觉隐藏 title（仅供屏幕阅读器）。
   * 用于上层组件自己绘制 header 的场景（如 common/Modal 有分隔线设计）。
   */
  hideTitleVisually?: boolean;
  /** 弹框尺寸 */
  size?: DialogSize;
  /** Backdrop 风格 */
  backdrop?: DialogBackdrop;
  /** 内容 padding：default = p-6 / none = 由上层组件管理 */
  padding?: DialogPadding;
  /** 是否显示右上角 X 关闭按钮 */
  showClose?: boolean;
  /**
   * 是否允许 ESC 键关闭（默认 true）
   * false 时拦截 Radix 默认的 ESC 关闭行为
   */
  closeOnEscape?: boolean;
  /**
   * 是否允许点击 backdrop 关闭（默认 true）
   * false 时拦截 Radix 默认的 outside click 关闭行为
   */
  closeOnOutsideClick?: boolean;
  /** 自定义 className（追加到 content 容器） */
  className?: string;
  /** 内容 */
  children: ReactNode;
}

const SIZE_CLASSES: Record<DialogSize, string> = {
  sm: 'max-w-sm',
  md: 'max-w-md',
  lg: 'max-w-2xl',
  xl: 'max-w-4xl',
  full: 'max-w-[min(95vw,1200px)]',
};

export function Dialog({
  open,
  onOpenChange,
  title,
  description,
  hideTitleVisually = false,
  size = 'md',
  backdrop = 'dim',
  padding = 'default',
  showClose = true,
  closeOnEscape = true,
  closeOnOutsideClick = true,
  className,
  children,
}: DialogProps) {
  return (
    <RadixDialog.Root open={open} onOpenChange={onOpenChange}>
      <AnimatePresence>
        {open && (
          <RadixDialog.Portal forceMount>
            <RadixDialog.Overlay asChild forceMount>
              <m.div
                className={cn(
                  'fixed inset-0 z-40',
                  backdrop === 'glass'
                    ? 'glass-strong'
                    : 'bg-black/50 backdrop-blur-sm',
                )}
                variants={fadeVariants}
                initial="initial"
                animate="animate"
                exit="exit"
              />
            </RadixDialog.Overlay>

            <RadixDialog.Content
              asChild
              forceMount
              // 拦截 ESC：preventDefault 阻止 Radix 默认关闭
              onEscapeKeyDown={(e) => {
                if (!closeOnEscape) e.preventDefault();
              }}
              // 拦截点击外部（pointer down outside content）
              onPointerDownOutside={(e) => {
                if (!closeOnOutsideClick) e.preventDefault();
              }}
              // 拦截"任意外部交互"（覆盖 focus 移出等场景）
              onInteractOutside={(e) => {
                if (!closeOnOutsideClick) e.preventDefault();
              }}
            >
              <m.div
                className={cn(
                  'fixed left-1/2 top-1/2 z-50 w-[92vw]',
                  'bg-[var(--c-modal-bg)]',
                  'border border-[var(--c-modal-border)]',
                  'rounded-[var(--c-modal-radius)]',
                  'shadow-[var(--c-modal-shadow)]',
                  'overflow-hidden',
                  'focus:outline-none',
                  padding === 'default' && 'p-6',
                  SIZE_CLASSES[size],
                  className,
                )}
                style={{
                  translate: '-50% -50%',
                  maxHeight: 'min(90vh, 800px)',
                }}
                variants={scaleVariants}
                initial="initial"
                animate="animate"
                exit="exit"
              >
                {/* 标题（可选）
                    - hideTitleVisually=true 时渲染 sr-only（a11y 但不显示）
                    - 默认渲染可见的 h2 */}
                {title && (
                  <RadixDialog.Title
                    className={
                      hideTitleVisually
                        ? 'sr-only'
                        : cn(
                            'text-lg text-[var(--s-text-primary)]',
                            'mb-2 pr-8',
                          )
                    }
                    style={
                      hideTitleVisually
                        ? undefined
                        : {
                            fontFamily: 'var(--s-font-heading)',
                            fontWeight: 'var(--s-weight-heading)',
                          }
                    }
                  >
                    {title}
                  </RadixDialog.Title>
                )}

                {/* 描述（可选，同时用于 aria-description）
                    Radix 要求每个 Dialog 都有 Description 元素，否则 console warning。
                    无 description 时渲染 sr-only 空 Description 兜底，
                    aria-hidden 让屏幕阅读器跳过（避免朗读冗余文本噪音） */}
                {description ? (
                  <RadixDialog.Description
                    className="text-sm text-[var(--s-text-secondary)] mb-4"
                  >
                    {description}
                  </RadixDialog.Description>
                ) : (
                  <RadixDialog.Description className="sr-only" aria-hidden="true" />
                )}

                {/* 没有 title 时，Radix 要求必须至少有 Title 供 aria，用 sr-only 兜底 */}
                {!title && (
                  <RadixDialog.Title className="sr-only">Dialog</RadixDialog.Title>
                )}

                {/* 右上角关闭按钮 */}
                {showClose && (
                  <RadixDialog.Close asChild>
                    <button
                      type="button"
                      aria-label="关闭"
                      className={cn(
                        'absolute right-4 top-4 z-10',
                        'rounded-full p-1.5',
                        'text-[var(--s-text-tertiary)]',
                        'hover:bg-[var(--s-hover)]',
                        'hover:text-[var(--s-text-primary)]',
                        'transition-colors',
                        'focus-visible:outline-none focus-visible:ring-2',
                        'focus-visible:ring-[var(--s-border-focus)]',
                      )}
                    >
                      <X className="w-4 h-4" aria-hidden="true" />
                    </button>
                  </RadixDialog.Close>
                )}

                {children}
              </m.div>
            </RadixDialog.Content>
          </RadixDialog.Portal>
        )}
      </AnimatePresence>
    </RadixDialog.Root>
  );
}

/**
 * Dialog Footer — 底部按钮组容器
 * 独立子组件，让外部可以方便地放 Action buttons
 */
export const DialogFooter = forwardRef<
  HTMLDivElement,
  React.HTMLAttributes<HTMLDivElement>
>(function DialogFooter({ className, ...rest }, ref) {
  return (
    <div
      ref={ref}
      className={cn('mt-6 flex items-center justify-end gap-2', className)}
      {...rest}
    />
  );
});

/** Re-export Close 供手动触发关闭 */
export const DialogClose = RadixDialog.Close;
