/**
 * 统一输入框组件
 *
 * 替代项目中 23+ 处重复的输入框 className 定义。
 * 支持 label、错误状态、前置图标。
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

export interface InputProps extends InputHTMLAttributes<HTMLInputElement> {
  /** 字段标签 */
  label?: string;
  /** 错误提示文字 */
  error?: string;
  /** 前置图标 */
  icon?: ReactNode;
  /** 是否撑满父容器宽度（默认 true） */
  fullWidth?: boolean;
}

const BASE_CLASSES = cn(
  'block px-3 py-2 text-sm rounded-lg',
  'bg-surface-elevated text-text-primary',
  'border border-border-default',
  'placeholder:text-text-tertiary',
  'transition-base',
  'focus:outline-none focus:border-focus-ring focus:ring-2 focus:ring-focus-ring/30',
  'disabled:opacity-50 disabled:pointer-events-none',
);

const ERROR_CLASSES = 'border-error focus:border-error focus:ring-error/30';

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

  return (
    <div className={fullWidth ? 'w-full' : 'inline-block'}>
      {label && (
        <label
          htmlFor={id}
          className="block text-sm font-medium text-text-secondary mb-1.5"
        >
          {label}
        </label>
      )}
      <div className="relative">
        {icon && (
          <span className="absolute left-3 top-1/2 -translate-y-1/2 text-text-tertiary pointer-events-none">
            {icon}
          </span>
        )}
        <input
          ref={ref}
          id={id}
          aria-invalid={!!error}
          aria-describedby={error ? `${id}-error` : undefined}
          className={cn(
            BASE_CLASSES,
            fullWidth && 'w-full',
            icon && 'pl-9',
            error && ERROR_CLASSES,
            className,
          )}
          {...rest}
        />
      </div>
      {error && (
        <p
          id={`${id}-error`}
          className="mt-1.5 text-xs text-error"
          role="alert"
        >
          {error}
        </p>
      )}
    </div>
  );
});
