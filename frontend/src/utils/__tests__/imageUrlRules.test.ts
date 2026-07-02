import { describe, expect, it } from 'vitest';
import {
  isThumbnailImageUrl,
  toDisplayThumbnailUrl,
  toOriginalImageUrl,
  toThumbnailImageUrl,
} from '../imageUrlRules';

describe('imageUrlRules', () => {
  it('removes OSS image process for original image URLs', () => {
    expect(toOriginalImageUrl(
      'https://cdn.everydayai.com.cn/workspace/a.png?x-oss-process=image/resize,w_360,m_lfit',
    )).toBe('https://cdn.everydayai.com.cn/workspace/a.png');
  });

  it('keeps non-process query params when restoring original image URLs', () => {
    expect(toOriginalImageUrl(
      'https://cdn.everydayai.com.cn/workspace/a.png?Expires=1&x-oss-process=image/resize,w_360,m_lfit&Signature=abc',
    )).toBe('https://cdn.everydayai.com.cn/workspace/a.png?Expires=1&Signature=abc');
  });

  it('uses original URL as thumbnail fallback without OSS processing params', () => {
    expect(toThumbnailImageUrl(
      'https://cdn.everydayai.com.cn/workspace/a.png?x-oss-process=image/resize,w_80,m_lfit',
      360,
    )).toBe('https://cdn.everydayai.com.cn/workspace/a.png');
  });

  it('does not append fill thumbnail params for fixed-size grids', () => {
    expect(toThumbnailImageUrl('https://cdn.everydayai.com.cn/workspace/a.png', 160, 'fill'))
      .toBe('https://cdn.everydayai.com.cn/workspace/a.png');
  });

  it('rejects workspace thumbnail objects as original image URLs', () => {
    expect(toOriginalImageUrl(
      'https://cdn.everydayai.com.cn/workspace-thumbnails/a.w360.webp',
    )).toBe('');
  });

  it('allows workspace thumbnail objects for thumbnail display only', () => {
    expect(isThumbnailImageUrl('https://cdn.everydayai.com.cn/workspace-thumbnails/a.w360.webp'))
      .toBe(true);
    expect(toDisplayThumbnailUrl(
      'https://cdn.everydayai.com.cn/workspace-thumbnails/a.w360.webp',
      'https://cdn.everydayai.com.cn/workspace/a.png',
    )).toBe('https://cdn.everydayai.com.cn/workspace-thumbnails/a.w360.webp');
  });
});
