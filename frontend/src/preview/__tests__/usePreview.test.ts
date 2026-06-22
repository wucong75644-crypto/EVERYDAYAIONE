/**
 * usePreview 状态机测试
 * @vitest-environment jsdom
 */
import { describe, it, expect } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { usePreview } from '../usePreview';
import type { PreviewItem } from '../types';

const a: PreviewItem = { filename: 'a.png' };
const b: PreviewItem = { filename: 'b.png' };
const c: PreviewItem = { filename: 'c.png' };

describe('usePreview', () => {
  it('初始状态 closed', () => {
    const { result } = renderHook(() => usePreview());
    expect(result.current.state).toEqual({ kind: 'closed' });
    expect(result.current.isOpen).toBe(false);
  });

  it('open 单个 item → kind=open, index=0', () => {
    const { result } = renderHook(() => usePreview());
    act(() => result.current.open(a));
    expect(result.current.state).toEqual({ kind: 'open', items: [a], index: 0 });
    expect(result.current.isOpen).toBe(true);
  });

  it('open 数组 + 指定 index', () => {
    const { result } = renderHook(() => usePreview());
    act(() => result.current.open([a, b, c], 2));
    expect(result.current.state).toEqual({ kind: 'open', items: [a, b, c], index: 2 });
  });

  it('open 越界 index → clamp 到合法范围', () => {
    const { result } = renderHook(() => usePreview());
    act(() => result.current.open([a, b], 10));
    expect(result.current.state).toEqual({ kind: 'open', items: [a, b], index: 1 });
    act(() => result.current.open([a, b], -5));
    expect(result.current.state).toEqual({ kind: 'open', items: [a, b], index: 0 });
  });

  it('open 空数组 → 不改状态', () => {
    const { result } = renderHook(() => usePreview());
    act(() => result.current.open([]));
    expect(result.current.state).toEqual({ kind: 'closed' });
  });

  it('close → 回到 closed', () => {
    const { result } = renderHook(() => usePreview());
    act(() => result.current.open(a));
    act(() => result.current.close());
    expect(result.current.state).toEqual({ kind: 'closed' });
  });

  it('setIndex 合法 → 更新', () => {
    const { result } = renderHook(() => usePreview());
    act(() => result.current.open([a, b, c], 0));
    act(() => result.current.setIndex(2));
    expect((result.current.state as { kind: 'open'; index: number }).index).toBe(2);
  });

  it('setIndex 越界 → 不变', () => {
    const { result } = renderHook(() => usePreview());
    act(() => result.current.open([a, b, c], 1));
    act(() => result.current.setIndex(-1));
    expect((result.current.state as { kind: 'open'; index: number }).index).toBe(1);
    act(() => result.current.setIndex(99));
    expect((result.current.state as { kind: 'open'; index: number }).index).toBe(1);
  });

  it('setIndex 在 closed 状态 → 无操作', () => {
    const { result } = renderHook(() => usePreview());
    act(() => result.current.setIndex(0));
    expect(result.current.state).toEqual({ kind: 'closed' });
  });
});
