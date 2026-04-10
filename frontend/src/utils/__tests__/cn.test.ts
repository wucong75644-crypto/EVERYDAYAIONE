/**
 * cn 工具函数单元测试
 */

import { describe, it, expect } from 'vitest';
import { cn } from '../cn';

describe('cn', () => {
  it('should join string arguments', () => {
    expect(cn('px-4', 'py-2')).toBe('px-4 py-2');
  });

  it('should skip falsy values', () => {
    expect(cn('btn', false, null, undefined, '')).toBe('btn');
  });

  it('should support conditional strings', () => {
    const isActive = true as boolean;
    const isDisabled = false as boolean;
    expect(cn('btn', isActive && 'active', isDisabled && 'disabled')).toBe('btn active');
  });

  it('should support object syntax', () => {
    expect(cn('btn', { active: true, disabled: false })).toBe('btn active');
  });

  it('should support nested arrays', () => {
    expect(cn('btn', ['size-md', 'rounded'])).toBe('btn size-md rounded');
  });

  it('should support deeply nested mixed inputs', () => {
    const flag = true as boolean;
    expect(
      cn(
        'base',
        ['nested', { inner: true, hidden: false }],
        null,
        flag && 'active'
      )
    ).toBe('base nested inner active');
  });

  it('should handle numbers', () => {
    expect(cn('col', 1, 2)).toBe('col 1 2');
  });

  it('should return empty string when all inputs are falsy', () => {
    expect(cn(false, null, undefined)).toBe('');
  });

  it('should handle empty input', () => {
    expect(cn()).toBe('');
  });

  it('should preserve order of class names', () => {
    expect(cn('a', 'b', 'c')).toBe('a b c');
  });
});
