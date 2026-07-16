/** Convert an unknown external value into stable, user-visible text. */
export function formatDisplayValue(value: unknown): string {
  if (value === null || value === undefined) return '';
  if (typeof value === 'string') return value;
  if (typeof value === 'number' || typeof value === 'boolean' || typeof value === 'bigint') {
    return String(value);
  }
  if (value instanceof Date) return Number.isNaN(value.getTime()) ? '' : value.toISOString();

  try {
    const serialized = JSON.stringify(value, (_key, nested) => (
      typeof nested === 'bigint' ? String(nested) : nested
    ));
    return serialized ?? '';
  } catch {
    return '[无法显示的结构化数据]';
  }
}

/** Form controls accept scalars only; structured values are rejected at the consumer boundary. */
export function formatFormValue(value: unknown): string {
  if (value === null || value === undefined) return '';
  if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') {
    return String(value);
  }
  return '';
}
