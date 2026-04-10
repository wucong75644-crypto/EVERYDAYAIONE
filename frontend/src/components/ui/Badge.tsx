/**
 * 统一徽章/标签组件
 *
 * 用于状态指示、计数、分类标签等场景。
 *
 * Variants:
 * - default : 中性灰
 * - accent  : 品牌色（蓝/赤陶）
 * - success : 成功（绿）
 * - error   : 错误（红）
 * - warning : 警告（黄）
 *
 * @example
 * ```tsx
 * <Badge variant="success">已订阅</Badge>
 * <Badge variant="accent" size="sm">免费</Badge>
 * ```
 */

import { type HTMLAttributes, type ReactNode } from 'react';
import { cn } from '../../utils/cn';

export type BadgeVariant = 'default' | 'accent' | 'success' | 'error' | 'warning';
export type BadgeSize = 'sm' | 'md';

export interface BadgeProps extends HTMLAttributes<HTMLSpanElement> {
  variant?: BadgeVariant;
  size?: BadgeSize;
  children: ReactNode;
}

const VARIANT_CLASSES: Record<BadgeVariant, string> = {
  default: 'bg-hover text-text-secondary',
  accent: 'bg-accent-light text-accent',
  success: 'bg-success-light text-success',
  error: 'bg-error-light text-error',
  warning: 'bg-warning-light text-warning',
};

const SIZE_CLASSES: Record<BadgeSize, string> = {
  sm: 'text-xs px-2 py-0.5',
  md: 'text-sm px-2.5 py-1',
};

export function Badge({
  variant = 'default',
  size = 'sm',
  className,
  children,
  ...rest
}: BadgeProps) {
  return (
    <span
      className={cn(
        'inline-flex items-center justify-center font-medium rounded-full',
        VARIANT_CLASSES[variant],
        SIZE_CLASSES[size],
        className,
      )}
      {...rest}
    >
      {children}
    </span>
  );
}
