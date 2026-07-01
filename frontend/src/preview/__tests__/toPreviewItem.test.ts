/**
 * 转换器测试 — 三种来源 → PreviewItem
 */
import { describe, it, expect } from 'vitest';
import { fromWorkspaceItem, fromFilePart, fromBlobImage, fromImageAsset } from '../toPreviewItem';

describe('fromWorkspaceItem', () => {
  it('完整字段映射', () => {
    const item = fromWorkspaceItem(
      {
        name: 'a.png',
        is_dir: false,
        size: 1024,
        modified: '0',
        cdn_url: 'https://cdn/a.png',
        mime_type: 'image/png',
      },
      '下载/a.png',
    );
    expect(item).toEqual({
      url: 'https://cdn/a.png',
      workspacePath: '下载/a.png',
      filename: 'a.png',
      mimeType: 'image/png',
      size: 1024,
    });
  });

  it('cdn_url 为 null → url 为 undefined', () => {
    const item = fromWorkspaceItem(
      { name: 'a.txt', is_dir: false, size: 0, modified: '0', cdn_url: null, mime_type: null },
      'a.txt',
    );
    expect(item.url).toBeUndefined();
  });
});

describe('fromFilePart', () => {
  it('完整字段映射', () => {
    expect(fromFilePart({
      type: 'file',
      url: 'https://cdn/b.pdf',
      name: 'b.pdf',
      mime_type: 'application/pdf',
      size: 2048,
      workspace_path: '上传/b.pdf',
    })).toEqual({
      url: 'https://cdn/b.pdf',
      workspacePath: '上传/b.pdf',
      filename: 'b.pdf',
      mimeType: 'application/pdf',
      size: 2048,
    });
  });
});

describe('fromBlobImage', () => {
  it('生成本地 blob 预览项 + 注入 image/* mimeType（防 ImageAdapter 漏匹配）', () => {
    const item = fromBlobImage({ previewUrl: 'blob:xxx', filename: 'photo' });
    expect(item).toEqual({
      url: 'blob:xxx',
      filename: 'photo',
      mimeType: 'image/*',
    });
  });
});

describe('fromImageAsset', () => {
  it('keeps original URL for preview and thumbnail URL for thumbnail strip', () => {
    const item = fromImageAsset({
      originalUrl: 'https://cdn.example.com/original.png',
      thumbnailUrl: 'https://cdn.example.com/thumb.png',
      filename: 'real.png',
    }, 'fallback.png');

    expect(item).toEqual({
      url: 'https://cdn.example.com/original.png',
      thumbnailUrl: 'https://cdn.example.com/thumb.png',
      filename: 'real.png',
      mimeType: 'image/*',
    });
  });
});
