/**
 * 统一按钮组件（V3 — cva + framer-motion）
 *
 * 替代项目中 30+ 处重复的按钮 className 定义。
 * 所有交互态（hover/active/focus-visible/disabled）统一处理。
 *
 * V3 升级要点（相对 V2）：
 * - 用 cva 定义 variants（类型安全 + IDE 自动完成）
 * - framer-motion spring hover + tap（替换纯 CSS scale，手感更"弹"）
 * - 新增 `glass` variant（毛玻璃背景）
 * - 圆角跟随主题（--s-radius-control）
 *
 * Variants:
 * - accent    : 主操作（跟随主题的品牌色）
 * - secondary : 次要操作（surface 底 + 边框）
 * - ghost     : 文字按钮（透明背景，hover 显示底色）
 * - danger    : 危险操作（红字）
 * - dark      : 深色背景按钮
 * - glass     : 毛玻璃按钮（浮层用）
 *
 * Sizes: sm | md | lg
 *
 * @example
 * ```tsx
 * <Button variant="accent" size="md" onClick={handleClick}>订阅</Button>
 * <Button variant="ghost" icon={<Trash2 />} loading={isDeleting}>删除</Button>
 * <Button variant="glass">浮层按钮</Button>
 * ```
 */

import { forwardRef, type ReactNode, type ComponentPropsWithoutRef } from 'react';
import { m } from 'framer-motion';
import { Loader2 } from 'lucide-react';
import { cn } from '../../utils/cn';
import { cva, type VariantProps } from '../../utils/variants';
import { SOFT_SPRING } from '../../utils/motion';

/* ============================================================
 * Variants 定义
 * ============================================================ */

export const buttonVariants = cva(
  cn(
    'inline-flex items-center justify-center',
    'font-medium select-none',
    'transition-colors duration-[var(--a-duration-normal)] ease-[var(--a-ease-out)]',
    // 键盘导航的 focus-visible 环（鼠标点击不显示）
    'focus-visible:outline-none focus-visible:ring-2',
    'focus-visible:ring-[var(--s-border-focus)] focus-visible:ring-offset-2',
    'focus-visible:ring-offset-[var(--s-surface-base)]',
    // 禁用态
    'disabled:opacity-50 disabled:pointer-events-none',
  ),
  {
    variants: {
      variant: {
        accent: cn(
          'bg-[var(--c-button-primary-bg)] text-[var(--c-button-primary-fg)]',
          'hover:bg-[var(--c-button-primary-bg-hover)]',
          'active:bg-[var(--c-button-primary-bg-active)]',
        ),
        secondary: cn(
          'bg-[var(--c-button-secondary-bg)] text-[var(--c-button-secondary-fg)]',
          'border border-[var(--c-button-secondary-border)]',
          'hover:bg-[var(--c-button-secondary-bg-hover)]',
        ),
        ghost: cn(
          'bg-[var(--c-button-ghost-bg)] text-[var(--c-button-ghost-fg)]',
          'hover:bg-[var(--c-button-ghost-bg-hover)]',
        ),
        danger: cn(
          'bg-transparent text-[var(--c-button-danger-fg)]',
          'hover:bg-[var(--c-button-danger-bg-hover)]',
        ),
        dark: cn(
          'bg-[var(--s-surface-inverse)] text-[var(--s-text-inverse)]',
          'hover:opacity-90',
        ),
        glass: cn(
          'glass text-[var(--s-text-primary)]',
          'hover:bg-[var(--s-hover)]',
        ),
      },
      size: {
        sm: 'px-3 py-1.5 text-sm gap-1.5 rounded-[var(--s-radius-control)]',
        md: 'px-4 py-2 text-sm gap-2 rounded-[var(--s-radius-control)]',
        lg: 'px-5 py-2.5 text-base gap-2 rounded-[var(--s-radius-control)]',
      },
      fullWidth: {
        true: 'w-full',
        false: '',
      },
    },
    defaultVariants: {
      variant: 'accent',
      size: 'md',
      fullWidth: false,
    },
  },
);

export type ButtonVariant = NonNullable<VariantProps<typeof buttonVariants>['variant']>;
export type ButtonSize = NonNullable<VariantProps<typeof buttonVariants>['size']>;

/* ============================================================
 * Props
 * ============================================================ */

export interface ButtonProps
  extends Omit<ComponentPropsWithoutRef<'button'>, 'onDrag' | 'onDragStart' | 'onDragEnd' | 'onAnimationStart' | 'onAnimationEnd' | 'onAnimationIteration'>,
    VariantProps<typeof buttonVariants> {
  /** 加载状态（显示 spinner，禁用点击） */
  loading?: boolean;
  /** 前置图标 */
  icon?: ReactNode;
}

/* ============================================================
 * Component
 * ============================================================ */

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  {
    variant,
    size,
    fullWidth,
    loading = false,
    icon,
    disabled,
    className,
    children,
    ...rest
  },
  ref,
) {
  const isDisabled = disabled || loading;

  return (
    <m.button
      ref={ref}
      disabled={isDisabled}
      className={cn(buttonVariants({ variant, size, fullWidth }), className)}
      // 仅在未禁用时加 gesture 动画，避免 disabled 状态下还有 hover 反馈
      whileHover={isDisabled ? undefined : { y: -1, scale: 1.02 }}
      whileTap={isDisabled ? undefined : { scale: 0.96, y: 0 }}
      transition={SOFT_SPRING}
      {...rest}
    >
      {loading ? (
        <Loader2 className="w-4 h-4 animate-spin" aria-hidden="true" />
      ) : (
        icon && <span className="inline-flex shrink-0">{icon}</span>
      )}
      {children}
    </m.button>
  );
});
