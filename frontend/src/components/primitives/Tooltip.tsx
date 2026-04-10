/**
 * Tooltip Primitive
 *
 * 基于 Radix UI Tooltip + framer-motion 的薄封装。
 *
 * 提供：
 * - 键盘聚焦也能显示（a11y）
 * - delay 可配置（默认 300ms）
 * - 箭头指示器
 * - 进出场动画（快速 fade + micro scale）
 * - 3 主题 token
 *
 * 使用前提：APP 根节点需要包 <TooltipProvider>，
 * 或者每个 Tooltip 自带 provider（这里选后者，避免侵入 App.tsx）。
 *
 * @example
 * ```tsx
 * <Tooltip content="删除此对话">
 *   <button><Trash2 /></button>
 * </Tooltip>
 * ```
 */

import { type ReactNode } from 'react';
import * as RadixTooltip from '@radix-ui/react-tooltip';
import { m } from 'framer-motion';
import { cn } from '../../utils/cn';

interface TooltipProps {
  content: ReactNode;
  children: ReactNode;
  /** 延迟显示（ms，默认 300） */
  delayDuration?: number;
  /** 侧边 */
  side?: 'top' | 'right' | 'bottom' | 'left';
  /** 对齐 */
  align?: 'start' | 'center' | 'end';
  /** 距离 trigger 的偏移（px） */
  sideOffset?: number;
  /** 是否禁用（用于条件显示） */
  disabled?: boolean;
}

export function Tooltip({
  content,
  children,
  delayDuration = 300,
  side = 'top',
  align = 'center',
  sideOffset = 6,
  disabled = false,
}: TooltipProps) {
  if (disabled) return <>{children}</>;

  return (
    <RadixTooltip.Provider delayDuration={delayDuration}>
      <RadixTooltip.Root>
        <RadixTooltip.Trigger asChild>{children}</RadixTooltip.Trigger>
        <RadixTooltip.Portal>
          <RadixTooltip.Content
            asChild
            side={side}
            align={align}
            sideOffset={sideOffset}
            collisionPadding={8}
          >
            <m.div
              className={cn(
                'z-[60] px-2.5 py-1.5',
                'rounded-md',
                'bg-[var(--s-surface-inverse)]',
                'text-[var(--s-text-inverse)]',
                'text-xs font-medium',
                'shadow-[var(--s-shadow-drop-lg)]',
                'max-w-xs pointer-events-none',
              )}
              initial={{ opacity: 0, scale: 0.92 }}
              animate={{ opacity: 1, scale: 1 }}
              exit={{ opacity: 0, scale: 0.92 }}
              transition={{ duration: 0.12, ease: [0.32, 0.72, 0, 1] }}
            >
              {content}
              <RadixTooltip.Arrow
                className="fill-[var(--s-surface-inverse)]"
                width={10}
                height={5}
              />
            </m.div>
          </RadixTooltip.Content>
        </RadixTooltip.Portal>
      </RadixTooltip.Root>
    </RadixTooltip.Provider>
  );
}
