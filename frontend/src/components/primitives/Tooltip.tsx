/**
 * Tooltip Primitive
 *
 * 基于 Radix UI Tooltip 的薄封装。
 *
 * 提供：
 * - 键盘聚焦也能显示（a11y）
 * - delay 可配置（默认 300ms）
 * - 箭头指示器
 * - 进出场动画（CSS data-state 驱动 — Radix 原生方案）
 * - 3 主题 token
 *
 * 为什么不用 framer-motion：
 * Radix Tooltip 没有暴露 controlled open API，open state 在内部管理。
 * 用 framer 的 AnimatePresence + open prop 需要双 hook（useState + Radix），
 * 反而复杂。Radix 推荐用 data-state="open|closed" 配 CSS keyframe，
 * 简单且 exit 动画能正确播放。
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
            side={side}
            align={align}
            sideOffset={sideOffset}
            collisionPadding={8}
            className={cn(
              'z-[60] px-2.5 py-1.5',
              'rounded-md',
              'bg-[var(--s-surface-inverse)]',
              'text-[var(--s-text-inverse)]',
              'text-xs font-medium',
              'shadow-[var(--s-shadow-drop-lg)]',
              'max-w-xs pointer-events-none',
              // Radix data-state 驱动的动画（origin 用 radix CSS var）
              // Radix Tooltip 有 3 个 state 值：
              // - delayed-open: 首次 hover 300ms 后打开
              // - instant-open: delay 窗口内连续 hover 其他 trigger 时瞬时打开
              // - closed: 关闭（退场）
              // 两种 open 都要触发入场动画，否则 toolbar 连续 hover 场景无动画
              'origin-[var(--radix-tooltip-content-transform-origin)]',
              'data-[state=delayed-open]:animate-tooltip-in',
              'data-[state=instant-open]:animate-tooltip-in',
              'data-[state=closed]:animate-tooltip-out',
            )}
          >
            {content}
            <RadixTooltip.Arrow
              className="fill-[var(--s-surface-inverse)]"
              width={10}
              height={5}
            />
          </RadixTooltip.Content>
        </RadixTooltip.Portal>
      </RadixTooltip.Root>
    </RadixTooltip.Provider>
  );
}
