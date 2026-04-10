/**
 * 统一卡片容器组件
 *
 * 替代项目中重复的 `bg-white rounded-xl border border-gray-200` 模式。
 *
 * Variants:
 * - default     : 默认卡片，浅色背景 + 边框
 * - elevated    : 浮起卡片，带阴影
 * - interactive : 可交互卡片，hover 时浮起（苹果风格）
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
import { cn } from '../../utils/cn';

export type CardVariant = 'default' | 'elevated' | 'interactive';
export type CardPadding = 'none' | 'sm' | 'md' | 'lg';

export interface CardProps extends HTMLAttributes<HTMLDivElement> {
  variant?: CardVariant;
  padding?: CardPadding;
}

const VARIANT_CLASSES: Record<CardVariant, string> = {
  default: 'bg-surface-card border border-border-default',
  elevated: 'bg-surface-card border border-border-light shadow-md',
  interactive: cn(
    'bg-surface-card border border-border-default',
    'cursor-pointer transition-base',
    'hover:border-border-default hover:shadow-md hover:-translate-y-0.5',
    'active:translate-y-0',
  ),
};

const PADDING_CLASSES: Record<CardPadding, string> = {
  none: '',
  sm: 'p-3',
  md: 'p-4',
  lg: 'p-6',
};

export const Card = forwardRef<HTMLDivElement, CardProps>(function Card(
  { variant = 'default', padding = 'md', className, children, ...rest },
  ref,
) {
  return (
    <div
      ref={ref}
      className={cn(
        'rounded-xl',
        VARIANT_CLASSES[variant],
        PADDING_CLASSES[padding],
        className,
      )}
      {...rest}
    >
      {children}
    </div>
  );
});
