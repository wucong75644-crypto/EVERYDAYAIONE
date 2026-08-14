import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { installChunkLoadRecovery } from '../chunkLoadRecovery';

describe('installChunkLoadRecovery', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    sessionStorage.clear();
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.useRealTimers();
  });

  it('reloads once when a Vite dynamic chunk cannot be loaded', () => {
    const reload = vi.fn();
    const cleanup = installChunkLoadRecovery(reload);
    const firstEvent = new Event('vite:preloadError', { cancelable: true });

    window.dispatchEvent(firstEvent);

    expect(firstEvent.defaultPrevented).toBe(true);
    expect(reload).toHaveBeenCalledOnce();

    const secondEvent = new Event('vite:preloadError', { cancelable: true });
    window.dispatchEvent(secondEvent);
    expect(secondEvent.defaultPrevented).toBe(false);
    expect(reload).toHaveBeenCalledOnce();
    cleanup();
  });

  it('allows recovery again after a healthy-page window', () => {
    const reload = vi.fn();
    const cleanup = installChunkLoadRecovery(reload);

    window.dispatchEvent(new Event('vite:preloadError', { cancelable: true }));
    vi.advanceTimersByTime(10_000);
    window.dispatchEvent(new Event('vite:preloadError', { cancelable: true }));

    expect(reload).toHaveBeenCalledTimes(2);
    cleanup();
  });
});
