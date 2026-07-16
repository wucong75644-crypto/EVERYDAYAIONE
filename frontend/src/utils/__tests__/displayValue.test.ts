import { describe, expect, it } from 'vitest';
import { formatDisplayValue, formatFormValue } from '../displayValue';

describe('formatDisplayValue', () => {
  it('preserves scalar values', () => {
    expect(formatDisplayValue('text')).toBe('text');
    expect(formatDisplayValue(42)).toBe('42');
    expect(formatDisplayValue(false)).toBe('false');
    expect(formatDisplayValue(null)).toBe('');
  });

  it('serializes structured values instead of using implicit object coercion', () => {
    expect(formatDisplayValue({ name: 'test', items: [1, 2] }))
      .toBe('{"name":"test","items":[1,2]}');
  });

  it('supports bigint nested in structured values', () => {
    expect(formatDisplayValue({ value: 10n })).toBe('{"value":"10"}');
  });

  it('returns an explicit fallback for circular values', () => {
    const value: { self?: unknown } = {};
    value.self = value;
    expect(formatDisplayValue(value)).toBe('[无法显示的结构化数据]');
  });
});

describe('formatFormValue', () => {
  it('accepts scalars and rejects structured values', () => {
    expect(formatFormValue(7)).toBe('7');
    expect(formatFormValue(true)).toBe('true');
    expect(formatFormValue({ value: 7 })).toBe('');
    expect(formatFormValue([1, 2])).toBe('');
  });
});
