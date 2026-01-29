/**
 * useModalAnimation Hook 单元测试
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useModalAnimation } from '../useModalAnimation';
import { MODAL_CLOSE_ANIMATION_DURATION } from '../../constants/animations';

describe('useModalAnimation', () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('should initialize with closed state', () => {
    const { result } = renderHook(() => useModalAnimation());

    expect(result.current.isOpen).toBe(false);
    expect(result.current.isClosing).toBe(false);
  });

  it('should open modal when open() is called', () => {
    const { result } = renderHook(() => useModalAnimation());

    act(() => {
      result.current.open();
    });

    expect(result.current.isOpen).toBe(true);
    expect(result.current.isClosing).toBe(false);
  });

  it('should start closing animation when close() is called', () => {
    const { result } = renderHook(() => useModalAnimation());

    // First open the modal
    act(() => {
      result.current.open();
    });

    // Then close it
    act(() => {
      result.current.close();
    });

    expect(result.current.isOpen).toBe(true);
    expect(result.current.isClosing).toBe(true);
  });

  it('should complete closing after animation duration', () => {
    const { result } = renderHook(() => useModalAnimation());

    // Open modal
    act(() => {
      result.current.open();
    });

    // Close modal
    act(() => {
      result.current.close();
    });

    expect(result.current.isClosing).toBe(true);

    // Fast forward time by animation duration
    act(() => {
      vi.advanceTimersByTime(MODAL_CLOSE_ANIMATION_DURATION);
    });

    expect(result.current.isOpen).toBe(false);
    expect(result.current.isClosing).toBe(false);
  });

  it('should use custom duration when provided', () => {
    const customDuration = 300;
    const { result } = renderHook(() => useModalAnimation({ duration: customDuration }));

    // Open and close modal
    act(() => {
      result.current.open();
    });

    act(() => {
      result.current.close();
    });

    expect(result.current.isClosing).toBe(true);

    // Fast forward by default duration (should still be closing)
    act(() => {
      vi.advanceTimersByTime(MODAL_CLOSE_ANIMATION_DURATION);
    });

    expect(result.current.isClosing).toBe(true);
    expect(result.current.isOpen).toBe(true);

    // Fast forward remaining time
    act(() => {
      vi.advanceTimersByTime(customDuration - MODAL_CLOSE_ANIMATION_DURATION);
    });

    expect(result.current.isOpen).toBe(false);
    expect(result.current.isClosing).toBe(false);
  });

  it('should call onClosed callback after closing completes', () => {
    const onClosed = vi.fn();
    const { result } = renderHook(() => useModalAnimation({ onClosed }));

    // Open and close modal
    act(() => {
      result.current.open();
    });

    act(() => {
      result.current.close();
    });

    expect(onClosed).not.toHaveBeenCalled();

    // Fast forward time
    act(() => {
      vi.advanceTimersByTime(MODAL_CLOSE_ANIMATION_DURATION);
    });

    expect(onClosed).toHaveBeenCalledTimes(1);
  });

  it('should reset closing state when opened during closing animation', () => {
    const { result } = renderHook(() => useModalAnimation());

    // Open modal
    act(() => {
      result.current.open();
    });

    // Start closing
    act(() => {
      result.current.close();
    });

    expect(result.current.isClosing).toBe(true);

    // Open again before closing completes
    act(() => {
      result.current.open();
    });

    expect(result.current.isOpen).toBe(true);
    expect(result.current.isClosing).toBe(false);
  });

  it('should handle multiple open/close cycles', () => {
    const { result } = renderHook(() => useModalAnimation());

    // First cycle
    act(() => {
      result.current.open();
    });
    expect(result.current.isOpen).toBe(true);

    act(() => {
      result.current.close();
    });
    act(() => {
      vi.advanceTimersByTime(MODAL_CLOSE_ANIMATION_DURATION);
    });
    expect(result.current.isOpen).toBe(false);

    // Second cycle
    act(() => {
      result.current.open();
    });
    expect(result.current.isOpen).toBe(true);

    act(() => {
      result.current.close();
    });
    act(() => {
      vi.advanceTimersByTime(MODAL_CLOSE_ANIMATION_DURATION);
    });
    expect(result.current.isOpen).toBe(false);
  });
});
