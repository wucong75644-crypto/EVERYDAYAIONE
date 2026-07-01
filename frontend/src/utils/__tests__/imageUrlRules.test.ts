import { describe, expect, it } from 'vitest';
import { toOriginalImageUrl, toThumbnailImageUrl } from '../imageUrlRules';

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

  it('builds thumbnail URL from the original URL', () => {
    expect(toThumbnailImageUrl(
      'https://cdn.everydayai.com.cn/workspace/a.png?x-oss-process=image/resize,w_80,m_lfit',
      360,
    )).toBe('https://cdn.everydayai.com.cn/workspace/a.png?x-oss-process=image/resize,w_360,m_lfit');
  });

  it('supports fill thumbnails for fixed-size grids', () => {
    expect(toThumbnailImageUrl('https://cdn.everydayai.com.cn/workspace/a.png', 160, 'fill'))
      .toBe('https://cdn.everydayai.com.cn/workspace/a.png?x-oss-process=image/resize,w_160,m_fill');
  });
});
