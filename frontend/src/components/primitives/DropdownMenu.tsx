/**
 * DropdownMenu Primitive
 *
 * 基于 Radix UI DropdownMenu + framer-motion 的薄封装。
 * 提供：
 * - 完整 a11y（roving tabindex / 方向键导航 / typeahead）
 * - Portal 渲染 + 碰撞检测（auto flip）
 * - 进出场动画（spring + stagger items）
 * - 毛玻璃 content 背景
 * - 3 主题 token
 *
 * 使用方式：
 * ```tsx
 * <DropdownMenu trigger={<Button>菜单</Button>}>
 *   <DropdownMenuItem onSelect={handleEdit}>编辑</DropdownMenuItem>
 *   <DropdownMenuItem onSelect={handleDelete} variant="danger">删除</DropdownMenuItem>
 * </DropdownMenu>
 * ```
 */

import { forwardRef, useState, type ReactNode } from 'react';
import * as RadixMenu from '@radix-ui/react-dropdown-menu';
import { AnimatePresence, m } from 'framer-motion';
import { cn } from '../../utils/cn';
import { slideDownVariants } from '../../utils/motion';

interface DropdownMenuProps {
  /** 触发器元素（按钮/图标/任意可聚焦元素） */
  trigger: ReactNode;
  /** 菜单对齐方式 */
  align?: 'start' | 'center' | 'end';
  /** 菜单相对 trigger 的侧边 */
  side?: 'top' | 'right' | 'bottom' | 'left';
  /** 菜单与 trigger 的距离（px） */
  sideOffset?: number;
  /** 是否使用毛玻璃背景 */
  glass?: boolean;
  /** 菜单内容 */
  children: ReactNode;
  /** 受控 open（可选，默认非受控） */
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
  /** 菜单最小宽度 */
  minWidth?: number;
}

export function DropdownMenu({
  trigger,
  align = 'start',
  side = 'bottom',
  sideOffset = 6,
  glass = false,
  children,
  open: openProp,
  onOpenChange,
  minWidth = 180,
}: DropdownMenuProps) {
  // 内部 open 状态，允许受控覆盖
  const [internalOpen, setInternalOpen] = useState(false);
  const open = openProp ?? internalOpen;
  const setOpen = (next: boolean) => {
    setInternalOpen(next);
    onOpenChange?.(next);
  };

  return (
    <RadixMenu.Root open={open} onOpenChange={setOpen}>
      <RadixMenu.Trigger asChild>{trigger}</RadixMenu.Trigger>

      <AnimatePresence>
        {open && (
          <RadixMenu.Portal forceMount>
            <RadixMenu.Content
              asChild
              forceMount
              align={align}
              side={side}
              sideOffset={sideOffset}
              collisionPadding={8}
              onCloseAutoFocus={(e) => e.preventDefault()}
            >
              <m.div
                className={cn(
                  'z-30 overflow-hidden',
                  'rounded-[var(--c-dropdown-radius)]',
                  'border border-[var(--c-dropdown-border)]',
                  'shadow-[var(--c-dropdown-shadow)]',
                  'py-1',
                  glass
                    ? 'glass'
                    : 'bg-[var(--c-dropdown-bg)]',
                )}
                style={{ minWidth }}
                variants={slideDownVariants}
                initial="initial"
                animate="animate"
                exit="exit"
              >
                {children}
              </m.div>
            </RadixMenu.Content>
          </RadixMenu.Portal>
        )}
      </AnimatePresence>
    </RadixMenu.Root>
  );
}

/* ============================================================
 * Item
 * ============================================================ */

interface DropdownMenuItemProps
  extends Omit<RadixMenu.DropdownMenuItemProps, 'asChild'> {
  /** 图标（可选，左侧） */
  icon?: ReactNode;
  /** 样式变体 */
  variant?: 'default' | 'danger';
}

export const DropdownMenuItem = forwardRef<
  HTMLDivElement,
  DropdownMenuItemProps
>(function DropdownMenuItem(
  { icon, variant = 'default', className, children, ...rest },
  ref,
) {
  return (
    <RadixMenu.Item
      ref={ref}
      className={cn(
        'flex items-center gap-2.5 px-3 py-2',
        'text-sm select-none outline-none cursor-pointer',
        'transition-colors duration-[var(--a-duration-fast)]',
        // hover / focus（键盘导航）共享相同反馈
        'data-[highlighted]:bg-[var(--c-dropdown-item-hover)]',
        'data-[disabled]:opacity-50 data-[disabled]:pointer-events-none',
        variant === 'default' &&
          'text-[var(--s-text-primary)] data-[highlighted]:text-[var(--s-text-primary)]',
        variant === 'danger' &&
          'text-[var(--s-error)] data-[highlighted]:bg-[var(--s-error-soft)]',
        className,
      )}
      {...rest}
    >
      {icon && (
        <span className="inline-flex shrink-0 w-4 h-4" aria-hidden="true">
          {icon}
        </span>
      )}
      <span className="flex-1">{children}</span>
    </RadixMenu.Item>
  );
});

/* ============================================================
 * Separator
 * ============================================================ */

export const DropdownMenuSeparator = forwardRef<
  HTMLDivElement,
  React.HTMLAttributes<HTMLDivElement>
>(function DropdownMenuSeparator({ className, ...rest }, ref) {
  return (
    <RadixMenu.Separator
      ref={ref}
      className={cn('h-px my-1 bg-[var(--s-border-default)]', className)}
      {...rest}
    />
  );
});

/* ============================================================
 * Label
 * ============================================================ */

export const DropdownMenuLabel = forwardRef<
  HTMLDivElement,
  React.HTMLAttributes<HTMLDivElement>
>(function DropdownMenuLabel({ className, ...rest }, ref) {
  return (
    <RadixMenu.Label
      ref={ref}
      className={cn(
        'px-3 py-1.5 text-xs font-medium text-[var(--s-text-tertiary)]',
        'uppercase tracking-wider',
        className,
      )}
      {...rest}
    />
  );
});
