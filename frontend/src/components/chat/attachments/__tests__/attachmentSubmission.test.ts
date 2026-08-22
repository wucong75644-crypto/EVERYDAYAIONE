import { describe, expect, it } from 'vitest';
import type { ChatAttachment } from '../ChatAttachment.types';
import { createAttachmentSubmissionSnapshot } from '../attachmentSubmission';

describe('createAttachmentSubmissionSnapshot', () => {
  it('所有图片来源都只提交原图 URL，缩略图只保留为元数据', () => {
    const sources = ['upload', 'quote', 'workspace'] as const;
    const attachments: ChatAttachment[] = sources.map((source) => ({
      id: `${source}:image`, sourceId: `${source}:image`, kind: 'image', source, status: 'ready',
      name: `${source}.png`, previewUrl: `https://cdn.example.com/${source}.thumb.webp`,
      thumbnailUrl: `https://cdn.example.com/${source}.thumb.webp`,
      originalUrl: `https://cdn.example.com/${source}.png`, mimeType: 'image/png', size: 10,
    }));

    const result = createAttachmentSubmissionSnapshot(attachments);

    expect(result.imageInputs.map((image) => image.url)).toEqual(
      sources.map((source) => `https://cdn.example.com/${source}.png`),
    );
    expect(result.imageInputs[0].thumbnail_url).toBe('https://cdn.example.com/upload.thumb.webp');
    expect(result.orderedAttachments.map((item) => item.kind)).toEqual(['image', 'image', 'image']);
  });

  it('上传中图片不提交，失败图片进入无效集合', () => {
    const base = {
      sourceId: 'image', kind: 'image' as const, source: 'workspace' as const,
      name: 'image.png', previewUrl: 'https://cdn.example.com/thumb.webp',
      originalUrl: null, mimeType: 'image/png', size: 10,
    };
    const result = createAttachmentSubmissionSnapshot([
      { ...base, id: 'uploading', status: 'uploading' },
      { ...base, id: 'error', status: 'error' },
    ]);

    expect(result.imageInputs).toEqual([]);
    expect(result.orderedAttachments).toEqual([]);
    expect(result.invalidImages.map((item) => item.id)).toEqual(['error']);
  });

  it('普通文件转换为聊天接口文件结构', () => {
    const attachment: ChatAttachment = {
      id: 'workspace:report', sourceId: 'docs/report.pdf', kind: 'file', source: 'workspace',
      status: 'ready', name: 'report.pdf', url: 'https://cdn.example.com/report.pdf',
      workspacePath: 'docs/report.pdf', mimeType: 'application/pdf', size: 2048,
    };

    expect(createAttachmentSubmissionSnapshot([attachment]).files).toEqual([{
      url: 'https://cdn.example.com/report.pdf', name: 'report.pdf',
      mime_type: 'application/pdf', size: 2048, workspace_path: 'docs/report.pdf',
    }]);
  });

  it('按附件数组顺序生成图片和文件混排提交内容', () => {
    const result = createAttachmentSubmissionSnapshot([
      {
        id: 'image:a', sourceId: 'a', kind: 'image', source: 'upload', status: 'ready',
        name: 'a.png', previewUrl: 'a', originalUrl: 'https://cdn/a.png',
      },
      {
        id: 'file:b', sourceId: 'b', kind: 'file', source: 'upload', status: 'ready',
        name: 'b.pdf', url: 'https://cdn/b.pdf', mimeType: 'application/pdf', size: 2,
      },
      {
        id: 'image:c', sourceId: 'c', kind: 'image', source: 'upload', status: 'ready',
        name: 'c.png', previewUrl: 'c', originalUrl: 'https://cdn/c.png',
      },
    ]);

    expect(result.orderedAttachments.map((item) => item.kind)).toEqual(['image', 'file', 'image']);
    expect(result.orderedAttachments.map((item) => (
      item.kind === 'image' ? item.image.url : item.file.url
    ))).toEqual(['https://cdn/a.png', 'https://cdn/b.pdf', 'https://cdn/c.png']);
  });
});
