/**
 * cva (class-variance-authority) 统一出口
 *
 * 项目内所有 variant 定义走这里，保持单一来源。
 *
 * @example
 * ```ts
 * import { cva, type VariantProps } from '@/utils/variants';
 *
 * export const buttonVariants = cva('base classes', {
 *   variants: {
 *     variant: { primary: '...', ghost: '...' },
 *     size: { sm: '...', md: '...' },
 *   },
 *   defaultVariants: { variant: 'primary', size: 'md' },
 * });
 *
 * type ButtonProps = VariantProps<typeof buttonVariants>;
 * ```
 */

export { cva, type VariantProps } from 'class-variance-authority';
