import { act, renderHook } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { useThumbnailFallback } from '../useThumbnailFallback';

describe('useThumbnailFallback', () => {
  it('falls back from thumbnail to original, then to failed', () => {
    const { result } = renderHook(() => useThumbnailFallback(
      'https://oss.example.com/workspace-thumbnails/a.w360.webp',
      'https://oss.example.com/workspace/a.png',
    ));

    expect(result.current.src).toContain('/workspace-thumbnails/');
    act(() => result.current.onError());
    expect(result.current.src).toBe('https://oss.example.com/workspace/a.png');
    expect(result.current.failed).toBe(false);
    act(() => result.current.onError());
    expect(result.current.src).toBe('');
    expect(result.current.failed).toBe(true);
  });

  it('uses original directly when no thumbnail exists', () => {
    const { result } = renderHook(() => useThumbnailFallback(
      null,
      'https://oss.example.com/workspace/a.png',
    ));

    expect(result.current.src).toBe('https://oss.example.com/workspace/a.png');
    act(() => result.current.onError());
    expect(result.current.failed).toBe(true);
  });

  it('resets when URLs change', () => {
    const { result, rerender } = renderHook(
      ({ thumbnail, original }) => useThumbnailFallback(thumbnail, original),
      {
        initialProps: {
          thumbnail: 'https://oss.example.com/workspace-thumbnails/a.w360.webp',
          original: 'https://oss.example.com/workspace/a.png',
        },
      },
    );
    act(() => result.current.onError());
    act(() => result.current.onError());

    rerender({
      thumbnail: 'https://oss.example.com/workspace-thumbnails/b.w360.webp',
      original: 'https://oss.example.com/workspace/b.png',
    });
    expect(result.current.src).toContain('/b.w360.webp');
    expect(result.current.failed).toBe(false);
  });

  it('does not retry the same URL twice', () => {
    const url = 'https://oss.example.com/workspace/a.png';
    const { result } = renderHook(() => useThumbnailFallback(url, url));

    act(() => result.current.onError());
    expect(result.current.failed).toBe(true);
  });
});
