/**
 * 统一按钮组件
 *
 * 替代项目中 30+ 处重复的按钮 className 定义。
 * 所有交互态（hover/active/focus-visible/disabled）统一处理。
 *
 * Variants:
 * - accent    : 主操作（蓝色/赤陶色，跟随主题）
 * - secondary : 次要操作（灰底）
 * - ghost     : 文字按钮（透明背景，hover 显示底色）
 * - danger    : 危险操作（红色）
 * - dark      : 深色背景按钮
 *
 * Sizes:
 * - sm : 紧凑（px-3 py-1.5 text-sm）
 * - md : 标准（px-4 py-2 text-sm）
 * - lg : 大（px-5 py-2.5 text-base）
 *
 * @example
 * ```tsx
 * <Button variant="accent" size="md" onClick={handleClick}>
 *   订阅
 * </Button>
 *
 * <Button variant="ghost" icon={<Trash2 />} loading={isDeleting}>
 *   删除
 * </Button>
 * ```
 */

import { forwardRef, type ButtonHTMLAttributes, type ReactNode } from 'react';
import { Loader2 } from 'lucide-react';
import { cn } from '../../utils/cn';

export type ButtonVariant = 'accent' | 'secondary' | 'ghost' | 'danger' | 'dark';
export type ButtonSize = 'sm' | 'md' | 'lg';

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  /** 按钮风格变体 */
  variant?: ButtonVariant;
  /** 按钮尺寸 */
  size?: ButtonSize;
  /** 加载状态（显示 spinner，禁用点击） */
  loading?: boolean;
  /** 前置图标 */
  icon?: ReactNode;
  /** 是否撑满父容器宽度 */
  fullWidth?: boolean;
}

/** variant 样式映射 */
const VARIANT_CLASSES: Record<ButtonVariant, string> = {
  accent:
    'bg-accent text-text-on-accent hover:bg-accent-hover active:bg-accent-hover',
  secondary:
    'bg-surface-card text-text-primary border border-border-default hover:bg-hover active:bg-active',
  ghost:
    'bg-transparent text-text-secondary hover:bg-hover active:bg-active',
  danger:
    'bg-transparent text-error hover:bg-error-light active:bg-error-light',
  dark:
    'bg-surface-dark-card text-text-on-dark hover:bg-surface-dark active:bg-surface-dark',
};

/** size 样式映射 */
const SIZE_CLASSES: Record<ButtonSize, string> = {
  sm: 'px-3 py-1.5 text-sm rounded-md gap-1.5',
  md: 'px-4 py-2 text-sm rounded-md gap-2',
  lg: 'px-5 py-2.5 text-base rounded-lg gap-2',
};

/** 共享样式 */
const BASE_CLASSES = cn(
  'inline-flex items-center justify-center',
  'font-medium select-none',
  'transition-base',
  // 触感反馈：按下微缩（苹果风格）
  'active:scale-[0.98]',
  // 键盘导航的 focus-visible 环（鼠标点击不显示）
  'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus-ring focus-visible:ring-offset-2',
  // 禁用态
  'disabled:opacity-50 disabled:pointer-events-none disabled:active:scale-100',
);

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  {
    variant = 'accent',
    size = 'md',
    loading = false,
    icon,
    fullWidth = false,
    disabled,
    className,
    children,
    ...rest
  },
  ref,
) {
  const isDisabled = disabled || loading;

  return (
    <button
      ref={ref}
      disabled={isDisabled}
      className={cn(
        BASE_CLASSES,
        VARIANT_CLASSES[variant],
        SIZE_CLASSES[size],
        fullWidth && 'w-full',
        className,
      )}
      {...rest}
    >
      {loading ? (
        <Loader2 className="w-4 h-4 animate-spin" aria-hidden="true" />
      ) : (
        icon && <span className="inline-flex shrink-0">{icon}</span>
      )}
      {children}
    </button>
  );
});
