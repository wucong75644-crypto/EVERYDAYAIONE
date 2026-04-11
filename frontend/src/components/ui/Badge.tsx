/**
 * 统一徽章/标签组件（V3 — cva + pulse variant）
 *
 * 用于状态指示、计数、分类标签等场景。
 *
 * V3 升级：
 * - cva variants
 * - 新增 `pulse` prop（触发脉冲循环动画，用于未读/在线指示）
 * - 颜色全部走 token
 *
 * Variants:
 * - default : 中性灰
 * - accent  : 品牌色（跟随主题）
 * - success : 成功（绿）
 * - error   : 错误（红）
 * - warning : 警告（黄）
 *
 * @example
 * ```tsx
 * <Badge variant="success">已订阅</Badge>
 * <Badge variant="accent" size="sm" pulse>在线</Badge>
 * ```
 */

import { forwardRef, type HTMLAttributes, type ReactNode } from 'react';
import { m } from 'framer-motion';
import { cn } from '../../utils/cn';
import { cva, type VariantProps } from '../../utils/variants';

export const badgeVariants = cva(
  cn(
    'inline-flex items-center justify-center',
    'font-medium rounded-full',
    'transition-colors duration-[var(--a-duration-normal)]',
  ),
  {
    variants: {
      variant: {
        default: cn(
          'bg-[var(--s-hover)]',
          'text-[var(--s-text-secondary)]',
        ),
        accent: cn(
          'bg-[var(--s-accent-soft)]',
          'text-[var(--s-accent)]',
        ),
        success: cn(
          'bg-[var(--s-success-soft)]',
          'text-[var(--s-success)]',
        ),
        error: cn(
          'bg-[var(--s-error-soft)]',
          'text-[var(--s-error)]',
        ),
        warning: cn(
          'bg-[var(--s-warning-soft)]',
          'text-[var(--s-warning)]',
        ),
      },
      size: {
        sm: 'text-xs px-2 py-0.5',
        md: 'text-sm px-2.5 py-1',
      },
    },
    defaultVariants: {
      variant: 'default',
      size: 'sm',
    },
  },
);

export type BadgeVariant = NonNullable<VariantProps<typeof badgeVariants>['variant']>;
export type BadgeSize = NonNullable<VariantProps<typeof badgeVariants>['size']>;

export interface BadgeProps
  extends Omit<HTMLAttributes<HTMLSpanElement>, 'onDrag' | 'onDragStart' | 'onDragEnd' | 'onAnimationStart' | 'onAnimationEnd' | 'onAnimationIteration'>,
    VariantProps<typeof badgeVariants> {
  children: ReactNode;
  /** 是否启用脉冲循环动画（用于状态指示器） */
  pulse?: boolean;
}

export const Badge = forwardRef<HTMLSpanElement, BadgeProps>(function Badge(
  { variant, size, className, children, pulse = false, ...rest },
  ref,
) {
  if (pulse) {
    return (
      <m.span
        ref={ref}
        className={cn(badgeVariants({ variant, size }), className)}
        animate={{ scale: [1, 1.05, 1], opacity: [1, 0.85, 1] }}
        transition={{
          duration: 1.8,
          ease: 'easeInOut',
          repeat: Infinity,
        }}
        {...rest}
      >
        {children}
      </m.span>
    );
  }

  return (
    <span
      ref={ref}
      className={cn(badgeVariants({ variant, size }), className)}
      {...rest}
    >
      {children}
    </span>
  );
});
