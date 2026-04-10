/**
 * useExitAnimation Hook 测试
 *
 * 覆盖：
 * - 初始状态（受 isOpen 影响）
 * - isOpen 切换 false → true 立即渲染
 * - isOpen 切换 true → false 延迟卸载
 * - 重复打开/关闭的状态正确性
 * - 卸载时清理定时器
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useExitAnimation } from '../useExitAnimation';

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
});

describe('useExitAnimation', () => {
  it('isOpen=false 初始时不渲染', () => {
    const { result } = renderHook(() => useExitAnimation(false, 150));
    expect(result.current.shouldRender).toBe(false);
    expect(result.current.isClosing).toBe(false);
  });

  it('isOpen=true 初始时立即渲染', () => {
    const { result } = renderHook(() => useExitAnimation(true, 150));
    expect(result.current.shouldRender).toBe(true);
    expect(result.current.isClosing).toBe(false);
  });

  it('isOpen 由 false → true 时立即渲染', () => {
    const { result, rerender } = renderHook(
      ({ isOpen }) => useExitAnimation(isOpen, 150),
      { initialProps: { isOpen: false } },
    );
    expect(result.current.shouldRender).toBe(false);

    rerender({ isOpen: true });
    expect(result.current.shouldRender).toBe(true);
    expect(result.current.isClosing).toBe(false);
  });

  it('isOpen 由 true → false 时进入退出动画状态，动画完成后卸载', () => {
    const { result, rerender } = renderHook(
      ({ isOpen }) => useExitAnimation(isOpen, 150),
      { initialProps: { isOpen: true } },
    );
    expect(result.current.shouldRender).toBe(true);

    rerender({ isOpen: false });
    // 退出动画期间 shouldRender 仍为 true
    expect(result.current.shouldRender).toBe(true);
    expect(result.current.isClosing).toBe(true);

    // 动画结束后卸载
    act(() => {
      vi.advanceTimersByTime(150);
    });
    expect(result.current.shouldRender).toBe(false);
    expect(result.current.isClosing).toBe(false);
  });

  it('退出动画期间再次打开应取消退出', () => {
    const { result, rerender } = renderHook(
      ({ isOpen }) => useExitAnimation(isOpen, 150),
      { initialProps: { isOpen: true } },
    );

    // 开始关闭
    rerender({ isOpen: false });
    expect(result.current.isClosing).toBe(true);

    // 50ms 后再次打开
    act(() => {
      vi.advanceTimersByTime(50);
    });
    rerender({ isOpen: true });

    expect(result.current.shouldRender).toBe(true);
    expect(result.current.isClosing).toBe(false);

    // 等到原本的退出动画时长到期，shouldRender 仍应为 true（被取消了）
    act(() => {
      vi.advanceTimersByTime(150);
    });
    expect(result.current.shouldRender).toBe(true);
  });

  it('支持自定义退出时长', () => {
    const { result, rerender } = renderHook(
      ({ isOpen }) => useExitAnimation(isOpen, 300),
      { initialProps: { isOpen: true } },
    );

    rerender({ isOpen: false });

    // 150ms 时仍在动画中
    act(() => {
      vi.advanceTimersByTime(150);
    });
    expect(result.current.shouldRender).toBe(true);

    // 300ms 后才卸载
    act(() => {
      vi.advanceTimersByTime(150);
    });
    expect(result.current.shouldRender).toBe(false);
  });

  it('多次开关循环正确', () => {
    const { result, rerender } = renderHook(
      ({ isOpen }) => useExitAnimation(isOpen, 100),
      { initialProps: { isOpen: false } },
    );

    // 第一次循环
    rerender({ isOpen: true });
    expect(result.current.shouldRender).toBe(true);

    rerender({ isOpen: false });
    act(() => {
      vi.advanceTimersByTime(100);
    });
    expect(result.current.shouldRender).toBe(false);

    // 第二次循环
    rerender({ isOpen: true });
    expect(result.current.shouldRender).toBe(true);

    rerender({ isOpen: false });
    act(() => {
      vi.advanceTimersByTime(100);
    });
    expect(result.current.shouldRender).toBe(false);
  });

  it('卸载时清理定时器（不抛出错误）', () => {
    const { rerender, unmount } = renderHook(
      ({ isOpen }) => useExitAnimation(isOpen, 150),
      { initialProps: { isOpen: true } },
    );

    rerender({ isOpen: false });
    // 退出动画进行中卸载
    expect(() => unmount()).not.toThrow();
  });
});
