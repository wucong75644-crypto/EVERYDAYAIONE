/**
 * className 条件拼接工具
 *
 * 用于替代项目中 148+ 处的模板字符串拼接，提高可读性和健壮性。
 *
 * 支持的输入类型：
 * - string：直接拼接
 * - false / null / undefined：自动跳过
 * - 对象：key 为 class 名，value 为 boolean
 * - 数组：递归处理
 *
 * @example
 * ```tsx
 * cn('px-4 py-2', isActive && 'bg-accent', { 'text-error': hasError })
 * // → 'px-4 py-2 bg-accent text-error'（当 isActive 和 hasError 都为 true 时）
 *
 * cn('btn', null, undefined, false, 'large')
 * // → 'btn large'
 * ```
 */

type ClassValue =
  | string
  | number
  | bigint
  | boolean
  | null
  | undefined
  | ClassValue[]
  | { [key: string]: boolean | null | undefined };

export function cn(...inputs: ClassValue[]): string {
  const classes: string[] = [];

  for (const input of inputs) {
    if (!input) continue;

    if (typeof input === 'string' || typeof input === 'number') {
      classes.push(String(input));
      continue;
    }

    if (Array.isArray(input)) {
      const nested = cn(...input);
      if (nested) classes.push(nested);
      continue;
    }

    if (typeof input === 'object') {
      for (const key in input) {
        if (input[key]) classes.push(key);
      }
    }
  }

  return classes.join(' ');
}
