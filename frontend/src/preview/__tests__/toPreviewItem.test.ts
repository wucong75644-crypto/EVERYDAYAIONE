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
        thumbnail_url: 'https://cdn/thumb.png',
        mime_type: 'image/png',
      },
      '下载/a.png',
    );
    expect(item).toEqual({
      url: 'https://cdn/a.png',
      thumbnailUrl: 'https://cdn/thumb.png',
      workspacePath: '下载/a.png',
      filename: 'a.png',
      mimeType: 'image/png',
      size: 1024,
    });
  });

  it('预览工作区图片时使用原图 URL', () => {
    const item = fromWorkspaceItem(
      {
        name: 'a.png',
        is_dir: false,
        size: 1024,
        modified: '0',
        cdn_url: 'https://cdn.everydayai.com.cn/workspace/a.png?x-oss-process=image/resize,w_360,m_lfit',
        mime_type: 'image/png',
      },
      '下载/a.png',
    );

    expect(item.url).toBe('https://cdn.everydayai.com.cn/workspace/a.png');
  });

  it('工作区图片有缩略图时主预览仍使用 cdn_url 原图', () => {
    const item = fromWorkspaceItem(
      {
        name: 'a.png',
        is_dir: false,
        size: 1024,
        modified: '0',
        cdn_url: 'https://cdn.everydayai.com.cn/workspace/a.png',
        thumbnail_url: 'https://cdn.everydayai.com.cn/workspace-thumbnails/a.w360.webp',
        mime_type: 'image/png',
      },
      '下载/a.png',
    );

    expect(item.url).toBe('https://cdn.everydayai.com.cn/workspace/a.png');
    expect(item.thumbnailUrl).toBe('https://cdn.everydayai.com.cn/workspace-thumbnails/a.w360.webp');
  });

  it('工作区 cdn_url 如果误传缩略图则不能作为主预览', () => {
    const item = fromWorkspaceItem(
      {
        name: 'a.png',
        is_dir: false,
        size: 1024,
        modified: '0',
        cdn_url: 'https://cdn.everydayai.com.cn/workspace-thumbnails/a.w360.webp',
        thumbnail_url: 'https://cdn.everydayai.com.cn/workspace-thumbnails/a.w360.webp',
        mime_type: 'image/png',
      },
      '下载/a.png',
    );

    expect(item.url).toBeUndefined();
    expect(item.thumbnailUrl).toBe('https://cdn.everydayai.com.cn/workspace-thumbnails/a.w360.webp');
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

  it('引用图输入框预览可用缩略图，但放大预览使用原图', () => {
    const item = fromBlobImage({
      previewUrl: 'https://cdn.everydayai.com.cn/workspace-thumbnails/a.w360.webp',
      originalUrl: 'https://cdn.everydayai.com.cn/workspace/a.png',
      thumbnailUrl: 'https://cdn.everydayai.com.cn/workspace-thumbnails/a.w360.webp',
      filename: 'quoted.png',
    });

    expect(item.url).toBe('https://cdn.everydayai.com.cn/workspace/a.png');
    expect(item.thumbnailUrl).toBe('https://cdn.everydayai.com.cn/workspace-thumbnails/a.w360.webp');
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
