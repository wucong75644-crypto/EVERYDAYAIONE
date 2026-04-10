/**
 * Popover Primitive
 *
 * 基于 Radix UI Popover + framer-motion 的薄封装。
 *
 * Popover vs DropdownMenu：
 * - DropdownMenu：只能放 item 列表，键盘方向键导航，ARIA role=menu
 * - Popover：可以放任意内容（表单/富文本/图表），tab 键导航，ARIA role=dialog
 *
 * 提供：
 * - Portal 渲染 + 碰撞检测（auto flip）
 * - 进出场动画（spring + scale）
 * - 可选毛玻璃背景
 * - 支持箭头指示器
 *
 * @example
 * ```tsx
 * <Popover trigger={<Button>设置</Button>}>
 *   <div>
 *     <Input label="名称" />
 *     <Button>保存</Button>
 *   </div>
 * </Popover>
 * ```
 */

import { useState, type ReactNode } from 'react';
import * as RadixPopover from '@radix-ui/react-popover';
import { AnimatePresence, m } from 'framer-motion';
import { cn } from '../../utils/cn';
import { slideDownVariants } from '../../utils/motion';

interface PopoverProps {
  trigger: ReactNode;
  children: ReactNode;
  align?: 'start' | 'center' | 'end';
  side?: 'top' | 'right' | 'bottom' | 'left';
  sideOffset?: number;
  alignOffset?: number;
  /** 显示箭头 */
  showArrow?: boolean;
  /** 毛玻璃背景 */
  glass?: boolean;
  /** 受控 open */
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
  /** 自定义 className（追加到 content） */
  className?: string;
  /** 最大宽度（px，默认 320） */
  maxWidth?: number;
}

export function Popover({
  trigger,
  children,
  align = 'center',
  side = 'bottom',
  sideOffset = 8,
  alignOffset = 0,
  showArrow = false,
  glass = false,
  open: openProp,
  onOpenChange,
  className,
  maxWidth = 320,
}: PopoverProps) {
  const [internalOpen, setInternalOpen] = useState(false);
  const open = openProp ?? internalOpen;
  const setOpen = (next: boolean) => {
    setInternalOpen(next);
    onOpenChange?.(next);
  };

  return (
    <RadixPopover.Root open={open} onOpenChange={setOpen}>
      <RadixPopover.Trigger asChild>{trigger}</RadixPopover.Trigger>

      <AnimatePresence>
        {open && (
          <RadixPopover.Portal forceMount>
            <RadixPopover.Content
              asChild
              forceMount
              align={align}
              alignOffset={alignOffset}
              side={side}
              sideOffset={sideOffset}
              collisionPadding={8}
              onOpenAutoFocus={(e) => e.preventDefault()}
            >
              <m.div
                className={cn(
                  'z-30 overflow-hidden',
                  'rounded-[var(--c-dropdown-radius)]',
                  'border border-[var(--c-dropdown-border)]',
                  'shadow-[var(--c-dropdown-shadow)]',
                  'p-4',
                  'focus:outline-none',
                  glass ? 'glass' : 'bg-[var(--c-dropdown-bg)]',
                  className,
                )}
                style={{ maxWidth }}
                variants={slideDownVariants}
                initial="initial"
                animate="animate"
                exit="exit"
              >
                {children}
                {showArrow && (
                  <RadixPopover.Arrow
                    className="fill-[var(--c-dropdown-bg)]"
                    width={12}
                    height={6}
                  />
                )}
              </m.div>
            </RadixPopover.Content>
          </RadixPopover.Portal>
        )}
      </AnimatePresence>
    </RadixPopover.Root>
  );
}

/** 直接 close popover 的快捷组件 */
export const PopoverClose = RadixPopover.Close;
