import { afterEach, describe, expect, it, vi } from 'vitest';

import { installChunkLoadRecovery } from '../chunkLoadRecovery';

describe('installChunkLoadRecovery', () => {
  afterEach(() => {
    sessionStorage.clear();
    vi.useRealTimers();
  });

  it('reloads once and prevents a preload error loop', () => {
    const reload = vi.fn();
    const cleanup = installChunkLoadRecovery(reload);
    const first = new Event('vite:preloadError', { cancelable: true });
    const second = new Event('vite:preloadError', { cancelable: true });

    window.dispatchEvent(first);
    window.dispatchEvent(second);

    expect(first.defaultPrevented).toBe(true);
    expect(second.defaultPrevented).toBe(false);
    expect(reload).toHaveBeenCalledOnce();
    cleanup();
  });

  it('allows one new recovery attempt after the guard window', () => {
    vi.useFakeTimers();
    const reload = vi.fn();
    const cleanup = installChunkLoadRecovery(reload);

    window.dispatchEvent(new Event('vite:preloadError', { cancelable: true }));
    vi.advanceTimersByTime(10_000);
    window.dispatchEvent(new Event('vite:preloadError', { cancelable: true }));

    expect(reload).toHaveBeenCalledTimes(2);
    cleanup();
  });
});
