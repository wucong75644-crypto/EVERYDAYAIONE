/**
 * fileCategory 工具函数单元测试
 *
 * 覆盖：categorize、matchesFilter、canPreviewImage、canPreviewVideo
 */

import { describe, it, expect } from 'vitest';
import {
  categorize,
  matchesFilter,
  IMAGE_EXTS,
  VIDEO_EXTS,
} from '../fileCategory';

describe('categorize', () => {
  describe('图片识别', () => {
    it.each(['png', 'jpg', 'jpeg', 'gif', 'webp', 'svg', 'bmp', 'avif', 'heic'])(
      'identifies .%s as image',
      (ext) => {
        expect(categorize({ name: `file.${ext}` })).toBe('image');
      },
    );

    it('大写扩展名也识别为图片', () => {
      expect(categorize({ name: 'IMG.PNG' })).toBe('image');
    });

    it('mime_type 兜底识别图片', () => {
      expect(categorize({ name: 'no-ext', mime_type: 'image/png' })).toBe('image');
    });
  });

  describe('视频识别', () => {
    it.each(['mp4', 'mov', 'webm', 'mkv', 'avi', 'm4v'])(
      'identifies .%s as video',
      (ext) => {
        expect(categorize({ name: `clip.${ext}` })).toBe('video');
      },
    );

    it('mime_type 兜底识别视频', () => {
      expect(categorize({ name: 'movie', mime_type: 'video/mp4' })).toBe('video');
    });
  });

  describe('文档兜底', () => {
    it.each(['xlsx', 'csv', 'pdf', 'docx', 'json', 'txt', 'md', 'py', 'zip'])(
      'identifies .%s as document',
      (ext) => {
        expect(categorize({ name: `file.${ext}` })).toBe('document');
      },
    );

    it('未知扩展名归类为 document', () => {
      expect(categorize({ name: 'mystery.xyz' })).toBe('document');
    });

    it('无扩展名归类为 document', () => {
      expect(categorize({ name: 'README' })).toBe('document');
    });

    it('mime_type 为 null 不影响判定', () => {
      expect(categorize({ name: 'file.pdf', mime_type: null })).toBe('document');
    });
  });

  describe('扩展名优先级高于 mime', () => {
    it('扩展名是图片但 mime 说是 video → 仍然图片', () => {
      expect(categorize({ name: 'a.png', mime_type: 'video/mp4' })).toBe('image');
    });
  });
});

describe('matchesFilter', () => {
  const img = { name: 'photo.png' };
  const vid = { name: 'clip.mp4' };
  const doc = { name: 'report.xlsx' };

  it('all 通过所有文件', () => {
    expect(matchesFilter(img, 'all')).toBe(true);
    expect(matchesFilter(vid, 'all')).toBe(true);
    expect(matchesFilter(doc, 'all')).toBe(true);
  });

  it('images 通过图片和视频，不通过文档', () => {
    expect(matchesFilter(img, 'images')).toBe(true);
    expect(matchesFilter(vid, 'images')).toBe(true);
    expect(matchesFilter(doc, 'images')).toBe(false);
  });

  it('documents 只通过文档', () => {
    expect(matchesFilter(img, 'documents')).toBe(false);
    expect(matchesFilter(vid, 'documents')).toBe(false);
    expect(matchesFilter(doc, 'documents')).toBe(true);
  });
});

// canPreviewImage / canPreviewVideo 已被预览适配器架构取代
// 见 src/preview/registry.ts canPreview() 与 resolveAdapter()

describe('白名单完整性', () => {
  it('图片白名单包含核心扩展名', () => {
    expect(IMAGE_EXTS.has('png')).toBe(true);
    expect(IMAGE_EXTS.has('jpg')).toBe(true);
    expect(IMAGE_EXTS.has('webp')).toBe(true);
  });

  it('视频白名单包含核心扩展名', () => {
    expect(VIDEO_EXTS.has('mp4')).toBe(true);
    expect(VIDEO_EXTS.has('mov')).toBe(true);
  });

  it('图片视频白名单互不相交', () => {
    for (const ext of IMAGE_EXTS) {
      expect(VIDEO_EXTS.has(ext)).toBe(false);
    }
  });
});
