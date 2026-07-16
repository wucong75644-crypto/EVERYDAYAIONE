import { describe, expect, it } from 'vitest';
import { fromUploadedFile, fromUploadedImage, fromWorkspaceFile } from '../attachmentAdapters';

describe('attachmentAdapters', () => {
  it('将上传图片转换为统一图片附件', () => {
    const file = new File(['image'], 'product.png', { type: 'image/png' });
    expect(fromUploadedImage({
      id: 'upload-1', file, preview: 'blob:preview', url: 'https://cdn/original.png',
      isUploading: false, error: null,
    })).toMatchObject({
      id: 'image:upload-1', kind: 'image', source: 'upload', sourceId: 'upload-1',
      status: 'ready', name: 'product.png', previewUrl: 'blob:preview',
      originalUrl: 'https://cdn/original.png', mimeType: 'image/png',
    });
  });

  it('保留引用图片来源和缩略图', () => {
    const file = new File([], 'quoted-image');
    expect(fromUploadedImage({
      id: 'quoted-1', file, preview: 'https://cdn/thumb.png', url: 'https://cdn/original.png',
      thumbnail_url: 'https://cdn/thumb.png', isUploading: false, error: null, isQuoted: true,
    })).toMatchObject({
      id: 'image:quoted-1', source: 'quote', previewUrl: 'https://cdn/thumb.png',
      originalUrl: 'https://cdn/original.png',
    });
  });

  it('将上传文件转换为统一文件附件', () => {
    const file = new File(['pdf'], 'report.pdf', { type: 'application/pdf' });
    expect(fromUploadedFile({
      id: 'file-1', file, name: file.name, size: file.size, mime_type: file.type,
      url: null, isUploading: true, error: null,
    })).toMatchObject({
      id: 'file:file-1', kind: 'file', source: 'upload', status: 'uploading',
      name: 'report.pdf', mimeType: 'application/pdf',
    });
  });

  it('按语义转换工作区图片并保留路径', () => {
    expect(fromWorkspaceFile({
      name: 'workspace.webp', workspace_path: '上传/workspace.webp',
      cdn_url: 'https://cdn/workspace.webp', mime_type: null, size: 10,
    })).toMatchObject({
      id: 'workspace:上传/workspace.webp', kind: 'image', source: 'workspace',
      status: 'ready', originalUrl: 'https://cdn/workspace.webp',
      workspacePath: '上传/workspace.webp',
    });
  });

  it('将无原图工作区图片标记为错误', () => {
    expect(fromWorkspaceFile({
      name: 'broken.jpg', workspace_path: 'broken.jpg', cdn_url: null,
      mime_type: 'image/jpeg', size: 10,
    })).toMatchObject({ kind: 'image', status: 'error', originalUrl: null });
  });
});
