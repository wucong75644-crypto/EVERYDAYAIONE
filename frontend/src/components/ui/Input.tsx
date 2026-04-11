/**
 * 统一输入框组件（V3 — cva + token 化）
 *
 * 替代项目中 23+ 处重复的输入框 className 定义。
 * 支持 label、错误状态、前置图标。
 *
 * V3 升级：
 * - cva variants
 * - 圆角跟随主题（--s-radius-control）
 * - 颜色全部走 --c-input-* token
 * - focus ring 扩散动画（纯 CSS transition，不需要 framer）
 *
 * @example
 * ```tsx
 * <Input
 *   label="邮箱"
 *   type="email"
 *   value={email}
 *   onChange={(e) => setEmail(e.target.value)}
 *   error={error}
 *   icon={<Mail size={16} />}
 * />
 * ```
 */

import { forwardRef, useId, type InputHTMLAttributes, type ReactNode } from 'react';
import { cn } from '../../utils/cn';
import { cva, type VariantProps } from '../../utils/variants';

export const inputVariants = cva(
  cn(
    'block px-3 py-2 text-sm',
    'rounded-[var(--c-input-radius)]',
    'bg-[var(--c-input-bg)]',
    'text-[var(--c-input-fg)]',
    'border border-[var(--c-input-border)]',
    'placeholder:text-[var(--c-input-placeholder)]',
    'transition-[border-color,box-shadow] duration-[var(--a-duration-normal)] ease-[var(--a-ease-out)]',
    'focus:outline-none',
    'focus:border-[var(--c-input-border-focus)]',
    'focus:shadow-[var(--c-input-ring-focus)]',
    'disabled:opacity-50 disabled:pointer-events-none',
  ),
  {
    variants: {
      state: {
        default: '',
        error: cn(
          'border-[var(--s-error)]',
          'focus:border-[var(--s-error)]',
          'focus:shadow-[0_0_0_3px_rgba(220,38,38,0.12)]',
        ),
      },
      fullWidth: {
        true: 'w-full',
        false: '',
      },
    },
    defaultVariants: {
      state: 'default',
      fullWidth: true,
    },
  },
);

export interface InputProps
  extends InputHTMLAttributes<HTMLInputElement>,
    Omit<VariantProps<typeof inputVariants>, 'state'> {
  /** 字段标签 */
  label?: string;
  /** 错误提示文字 */
  error?: string;
  /** 前置图标 */
  icon?: ReactNode;
}

export const Input = forwardRef<HTMLInputElement, InputProps>(function Input(
  {
    label,
    error,
    icon,
    fullWidth = true,
    className,
    id: providedId,
    ...rest
  },
  ref,
) {
  const generatedId = useId();
  const id = providedId || generatedId;
  const state = error ? 'error' : 'default';

  return (
    <div className={fullWidth ? 'w-full' : 'inline-block'}>
      {label && (
        <label
          htmlFor={id}
          className="block text-sm font-medium text-[var(--s-text-secondary)] mb-1.5"
        >
          {label}
        </label>
      )}
      <div className="relative">
        {icon && (
          <span className="absolute left-3 top-1/2 -translate-y-1/2 text-[var(--s-text-tertiary)] pointer-events-none">
            {icon}
          </span>
        )}
        <input
          ref={ref}
          id={id}
          aria-invalid={!!error}
          aria-describedby={error ? `${id}-error` : undefined}
          className={cn(
            inputVariants({ state, fullWidth }),
            icon && 'pl-9',
            className,
          )}
          {...rest}
        />
      </div>
      {error && (
        <p
          id={`${id}-error`}
          className="mt-1.5 text-xs text-[var(--s-error)]"
          role="alert"
        >
          {error}
        </p>
      )}
    </div>
  );
});
