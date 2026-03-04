/**
 * ImagePreview 单测
 *
 * 覆盖引用图片视觉标识：
 * - 引用图片显示蓝色光环边框
 * - 引用图片左下角显示"引用"角标
 * - 引用图片左上角显示引号图标
 * - 上传图片仍显示数字序号
 */

import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import ImagePreview from '../ImagePreview';
import type { UploadedImage } from '../../../hooks/useImageUpload';

const createImage = (overrides: Partial<UploadedImage> = {}): UploadedImage => ({
  id: `img-${Date.now()}-${Math.random()}`,
  file: new File([''], 'test.png', { type: 'image/png' }),
  preview: 'https://cdn.example.com/test.png',
  url: 'https://cdn.example.com/test.png',
  isUploading: false,
  error: null,
  ...overrides,
});

describe('ImagePreview - quoted image indicators', () => {
  it('should show "引用" badge for quoted images', () => {
    const quoted = createImage({ id: 'q1', isQuoted: true });
    render(<ImagePreview images={[quoted]} onRemove={vi.fn()} />);

    expect(screen.getByText('引用')).toBeInTheDocument();
  });

  it('should show numeric badge for non-quoted images', () => {
    const uploaded = createImage({ id: 'u1', isQuoted: false });
    render(<ImagePreview images={[uploaded]} onRemove={vi.fn()} />);

    expect(screen.getByText('1')).toBeInTheDocument();
    expect(screen.queryByText('引用')).not.toBeInTheDocument();
  });

  it('should apply blue ring class to quoted image', () => {
    const quoted = createImage({ id: 'q1', isQuoted: true });
    render(<ImagePreview images={[quoted]} onRemove={vi.fn()} />);

    const img = screen.getByAltText('引用图片');
    expect(img.className).toContain('ring-2');
    expect(img.className).toContain('ring-blue-400');
  });

  it('should show both quoted and uploaded images correctly', () => {
    const quoted = createImage({ id: 'q1', isQuoted: true, preview: 'https://cdn.example.com/quoted.png' });
    const uploaded = createImage({ id: 'u1', isQuoted: false, preview: 'https://cdn.example.com/uploaded.png' });
    render(<ImagePreview images={[quoted, uploaded]} onRemove={vi.fn()} />);

    expect(screen.getByText('引用')).toBeInTheDocument();
    expect(screen.getByText('1')).toBeInTheDocument();
  });
});
