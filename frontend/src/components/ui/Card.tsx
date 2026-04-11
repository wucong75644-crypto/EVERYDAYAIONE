/**
 * 统一卡片容器组件（V3 — cva + framer）
 *
 * 替代项目中重复的 `bg-white rounded-xl border border-gray-200` 模式。
 *
 * V3 升级：
 * - cva variants
 * - interactive variant 用 framer spring hover（替换纯 CSS translate）
 * - 圆角跟随主题 (--s-radius-card)
 *
 * Variants:
 * - default     : 默认卡片（surface + 细边）
 * - elevated    : 浮起卡片（whisper shadow）
 * - interactive : 可交互卡片（hover spring 浮起）
 * - glass       : 毛玻璃卡片（浮层用）
 *
 * @example
 * ```tsx
 * <Card variant="interactive" padding="md" onClick={handleClick}>
 *   <h3>标题</h3>
 *   <p>内容</p>
 * </Card>
 * ```
 */

import { forwardRef, type HTMLAttributes } from 'react';
import { m } from 'framer-motion';
import { cn } from '../../utils/cn';
import { cva, type VariantProps } from '../../utils/variants';
import { SOFT_SPRING } from '../../utils/motion';

export const cardVariants = cva(
  cn(
    'rounded-[var(--s-radius-card)]',
    'transition-colors duration-[var(--a-duration-normal)]',
  ),
  {
    variants: {
      variant: {
        default: cn(
          'bg-[var(--c-card-bg)]',
          'border border-[var(--c-card-border)]',
        ),
        elevated: cn(
          'bg-[var(--c-card-bg)]',
          'border border-[var(--s-border-subtle)]',
          'shadow-[var(--s-shadow-whisper)]',
        ),
        interactive: cn(
          'bg-[var(--c-card-bg)]',
          'border border-[var(--c-card-border)]',
          'cursor-pointer',
          'shadow-[var(--c-card-shadow)]',
          'hover:shadow-[var(--c-card-shadow-hover)]',
          'hover:border-[var(--s-border-strong)]',
        ),
        glass: cn('glass'),
      },
      padding: {
        none: '',
        sm: 'p-3',
        md: 'p-4',
        lg: 'p-6',
      },
    },
    defaultVariants: {
      variant: 'default',
      padding: 'md',
    },
  },
);

export type CardVariant = NonNullable<VariantProps<typeof cardVariants>['variant']>;
export type CardPadding = NonNullable<VariantProps<typeof cardVariants>['padding']>;

export interface CardProps
  extends Omit<HTMLAttributes<HTMLDivElement>, 'onDrag' | 'onDragStart' | 'onDragEnd' | 'onAnimationStart' | 'onAnimationEnd' | 'onAnimationIteration'>,
    VariantProps<typeof cardVariants> {}

export const Card = forwardRef<HTMLDivElement, CardProps>(function Card(
  { variant, padding, className, children, ...rest },
  ref,
) {
  const isInteractive = variant === 'interactive';

  return (
    <m.div
      ref={ref}
      className={cn(cardVariants({ variant, padding }), className)}
      whileHover={isInteractive ? { y: -2 } : undefined}
      whileTap={isInteractive ? { y: 0, scale: 0.995 } : undefined}
      transition={SOFT_SPRING}
      {...rest}
    >
      {children}
    </m.div>
  );
});
