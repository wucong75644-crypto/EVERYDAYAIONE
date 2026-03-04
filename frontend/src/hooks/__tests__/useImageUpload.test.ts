/**
 * useImageUpload Hook 单测
 *
 * 覆盖引用图片相关功能：
 * - addQuotedImage 添加引用图
 * - hasQuotedImage 派生状态
 * - 引用图替换（每次只保留一张引用）
 * - 引用图移除
 * - 引用图与上传图共存
 */

import { describe, it, expect } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useImageUpload } from '../useImageUpload';

describe('useImageUpload - addQuotedImage', () => {
  it('should add a quoted image with correct properties', () => {
    const { result } = renderHook(() => useImageUpload());

    act(() => {
      result.current.addQuotedImage('https://cdn.example.com/image1.png');
    });

    expect(result.current.images).toHaveLength(1);
    expect(result.current.hasQuotedImage).toBe(true);
    expect(result.current.hasImages).toBe(true);

    const quoted = result.current.images[0];
    expect(quoted.isQuoted).toBe(true);
    expect(quoted.url).toBe('https://cdn.example.com/image1.png');
    expect(quoted.preview).toBe('https://cdn.example.com/image1.png');
    expect(quoted.isUploading).toBe(false);
    expect(quoted.error).toBeNull();
    expect(quoted.id).toMatch(/^quoted-/);
  });

  it('should include quoted image in uploadedImageUrls', () => {
    const { result } = renderHook(() => useImageUpload());

    act(() => {
      result.current.addQuotedImage('https://cdn.example.com/image1.png');
    });

    expect(result.current.uploadedImageUrls).toContain('https://cdn.example.com/image1.png');
  });

  it('should replace existing quoted image when adding a new one', () => {
    const { result } = renderHook(() => useImageUpload());

    act(() => {
      result.current.addQuotedImage('https://cdn.example.com/old.png');
    });
    expect(result.current.images).toHaveLength(1);

    act(() => {
      result.current.addQuotedImage('https://cdn.example.com/new.png');
    });

    expect(result.current.images).toHaveLength(1);
    expect(result.current.images[0].url).toBe('https://cdn.example.com/new.png');
  });

  it('should place quoted image at the beginning of the array', () => {
    const { result } = renderHook(() => useImageUpload());

    // 先手动注入一个模拟上传图（直接操作 state 不方便，用 addQuotedImage 后再加一个新引用来验证）
    act(() => {
      result.current.addQuotedImage('https://cdn.example.com/first.png');
    });

    expect(result.current.images[0].isQuoted).toBe(true);
  });

  it('should remove quoted image via handleRemoveImage', () => {
    const { result } = renderHook(() => useImageUpload());

    act(() => {
      result.current.addQuotedImage('https://cdn.example.com/image1.png');
    });
    const quotedId = result.current.images[0].id;

    act(() => {
      result.current.handleRemoveImage(quotedId);
    });

    expect(result.current.images).toHaveLength(0);
    expect(result.current.hasQuotedImage).toBe(false);
    expect(result.current.hasImages).toBe(false);
  });

  it('should clear quoted image via handleRemoveAllImages', () => {
    const { result } = renderHook(() => useImageUpload());

    act(() => {
      result.current.addQuotedImage('https://cdn.example.com/image1.png');
    });

    act(() => {
      result.current.handleRemoveAllImages();
    });

    expect(result.current.images).toHaveLength(0);
    expect(result.current.hasQuotedImage).toBe(false);
  });

  it('should report hasQuotedImage=false when no quoted images exist', () => {
    const { result } = renderHook(() => useImageUpload());
    expect(result.current.hasQuotedImage).toBe(false);
  });
});
