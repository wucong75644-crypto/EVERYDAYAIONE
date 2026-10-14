import { act, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useStreamingText } from '../useStreamingText';

describe('streaming display pacing', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.stubGlobal('requestAnimationFrame', (callback: FrameRequestCallback) =>
      setTimeout(() => callback(performance.now()), 16));
    vi.stubGlobal('cancelAnimationFrame', clearTimeout);
  });
  afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });

  it('shows first characters immediately and spreads a large chunk over frames', () => {
    const text = '甜品烘焙坊的完整介绍'.repeat(20);
    const { result, rerender } = renderHook(({ value }) => useStreamingText(value, true), { initialProps: { value: '' } });
    rerender({ value: text });
    expect(result.current).toBe(text.slice(0, 6));
    act(() => vi.advanceTimersByTime(16));
    expect(result.current.length).toBeGreaterThan(6);
    expect(result.current.length).toBeLessThan(text.length);
    act(() => vi.advanceTimersByTime(128));
    expect(result.current).toBe(text);
  });

  it('appends consecutive chunks exactly once and never moves backwards', () => {
    const { result, rerender } = renderHook(({ text }) => useStreamingText(text, true), { initialProps: { text: '' } });
    rerender({ text: '你好，第一段' });
    const before = result.current;
    rerender({ text: '你好，第一段；第二段来了。'.repeat(10) });
    act(() => vi.advanceTimersByTime(16));
    expect(result.current.startsWith(before)).toBe(true);
    act(() => vi.advanceTimersByTime(128));
    expect(result.current).toBe('你好，第一段；第二段来了。'.repeat(10));
  });

  it.each(['completed', 'paused', 'cancelled', 'failed'])('reveals all received text immediately on %s', () => {
    const text = '已经收到的内容'.repeat(30);
    const { result, rerender } = renderHook(({ value, active }) => useStreamingText(value, active), { initialProps: { value: '', active: true } });
    rerender({ value: text, active: true });
    expect(result.current.length).toBeLessThan(text.length);
    rerender({ value: text, active: false });
    expect(result.current).toBe(text);
    act(() => vi.advanceTimersByTime(200));
    expect(result.current).toBe(text);
  });

  it('replaces snapshots immediately without mixing old text', () => {
    const { result, rerender } = renderHook(({ text }) => useStreamingText(text, true), { initialProps: { text: '旧文本'.repeat(30) } });
    rerender({ text: '恢复后的完整快照' });
    expect(result.current).toBe('恢复后的完整快照');
  });

  it('does not split surrogate pairs and cancels work on unmount', () => {
    const text = 'abcde😀'.repeat(30);
    const { result, rerender, unmount } = renderHook(({ value }) => useStreamingText(value, true), { initialProps: { value: '' } });
    rerender({ value: text });
    expect(result.current).toBe('abcde😀');
    unmount();
    expect(vi.getTimerCount()).toBe(0);
  });

  it('does not animate history or reduced-motion output', () => {
    const history = renderHook(() => useStreamingText('历史快照'.repeat(30), true));
    expect(history.result.current).toBe('历史快照'.repeat(30));
    history.unmount();
    vi.stubGlobal('matchMedia', () => ({ matches: true }));
    const text = '完整内容'.repeat(30);
    const { result } = renderHook(() => useStreamingText(text, true));
    expect(result.current).toBe(text);
    expect(vi.getTimerCount()).toBe(0);
  });
});
