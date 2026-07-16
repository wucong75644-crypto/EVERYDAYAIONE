import { describe, expect, it } from 'vitest';
import { hasValidWorkspaceImage, normalizeSubmissionAttachments } from '../attachmentNormalization';

describe('normalizeSubmissionAttachments', () => {
  it('仅将有可用原图地址的工作区图片计入模型图片状态', () => {
    const base = { name: 'product.png', workspace_path: '上传/product.png', mime_type: 'image/png', size: 10 };
    expect(hasValidWorkspaceImage([{ ...base, cdn_url: 'https://cdn.example.com/product.png' }])).toBe(true);
    expect(hasValidWorkspaceImage([{ ...base, cdn_url: null }])).toBe(false);
    expect(hasValidWorkspaceImage([{ ...base, name: 'report.pdf', mime_type: 'application/pdf', cdn_url: 'https://cdn.example.com/report.pdf' }])).toBe(false);
  });

  it('按文件语义分流工作区图片与普通文件', () => {
    const result = normalizeSubmissionAttachments({
      uploadedImageUrls: [],
      uploadedImages: [],
      uploadedFiles: [],
      workspaceFiles: [
        {
          name: 'product.png', workspace_path: '上传/product.png',
          cdn_url: 'https://cdn.example.com/product.png', mime_type: 'image/png', size: 10,
        },
        {
          name: 'report.pdf', workspace_path: '上传/report.pdf',
          cdn_url: 'https://cdn.example.com/report.pdf', mime_type: 'application/pdf', size: 20,
        },
      ],
    });

    expect(result.imageInputs).toEqual([{
      url: 'https://cdn.example.com/product.png',
      original_url: 'https://cdn.example.com/product.png',
      name: 'product.png',
      workspace_path: '上传/product.png',
      mime_type: 'image/png',
      size: 10,
    }]);
    expect(result.imageUrls).toEqual(['https://cdn.example.com/product.png']);
    expect(result.files).toEqual([{
      url: 'https://cdn.example.com/report.pdf',
      name: 'report.pdf',
      mime_type: 'application/pdf',
      size: 20,
      workspace_path: '上传/report.pdf',
    }]);
  });

  it('在 MIME 缺失时使用扩展名识别图片', () => {
    const result = normalizeSubmissionAttachments({
      uploadedImageUrls: [], uploadedImages: [], uploadedFiles: [],
      workspaceFiles: [{
        name: 'reference.webp', workspace_path: 'reference.webp',
        cdn_url: 'https://cdn.example.com/reference.webp', mime_type: null, size: 10,
      }],
    });

    expect(result.imageUrls).toEqual(['https://cdn.example.com/reference.webp']);
    expect(result.files).toEqual([]);
  });

  it('记录缺少有效原图 URL 的工作区图片并保留文件降级', () => {
    const image = {
      name: 'broken.jpg', workspace_path: 'broken.jpg',
      cdn_url: 'https://cdn.example.com/workspace-thumbnails/broken.w360.webp',
      mime_type: 'image/jpeg', size: 10,
    };
    const result = normalizeSubmissionAttachments({
      uploadedImageUrls: [], uploadedImages: [], uploadedFiles: [], workspaceFiles: [image],
    });

    expect(result.imageInputs).toEqual([]);
    expect(result.invalidWorkspaceImages).toEqual([image]);
    expect(result.files[0]).toMatchObject({ name: 'broken.jpg', url: '' });
  });

  it('合并上传图片与工作区图片且不重复已有元数据', () => {
    const result = normalizeSubmissionAttachments({
      uploadedImageUrls: ['https://cdn.example.com/upload.png'],
      uploadedImages: [{
        url: 'https://cdn.example.com/upload.png',
        workspace_path: '上传/upload.png',
      }],
      uploadedFiles: [],
      workspaceFiles: [{
        name: 'workspace.png', workspace_path: '上传/workspace.png',
        cdn_url: 'https://cdn.example.com/workspace.png', mime_type: 'image/png', size: 10,
      }],
    });

    expect(result.imageInputs).toHaveLength(2);
    expect(result.imageUrls).toEqual([
      'https://cdn.example.com/upload.png',
      'https://cdn.example.com/workspace.png',
    ]);
  });
});
