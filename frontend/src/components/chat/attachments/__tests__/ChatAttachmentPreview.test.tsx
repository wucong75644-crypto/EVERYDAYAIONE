import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import ChatAttachmentPreview from '../ChatAttachmentPreview';
import type { ChatAttachment } from '../ChatAttachment.types';

const previewMocks = vi.hoisted(() => ({
  open: vi.fn(), close: vi.fn(), setIndex: vi.fn(),
}));
vi.mock('../../../../preview/PreviewHost', () => ({ default: () => null }));
vi.mock('../../../../preview/usePreview', () => ({
  usePreview: () => ({
    state: { kind: 'closed' }, ...previewMocks,
  }),
}));

function workspaceImage(): ChatAttachment {
  return {
    id: 'workspace:images/product.png',
    kind: 'image',
    source: 'workspace',
    status: 'ready',
    name: 'product.png',
    mimeType: 'image/png',
    size: 1024,
    previewUrl: 'https://cdn.example.com/product.thumbnail.webp',
    originalUrl: 'https://cdn.example.com/product.png',
    workspacePath: 'images/product.png',
  };
}

describe('ChatAttachmentPreview', () => {
  it('工作区图片只显示缩略图，不渲染文件名卡片', () => {
    render(<ChatAttachmentPreview attachments={[workspaceImage()]} onRemove={vi.fn()} />);

    expect(screen.getByRole('img', { name: 'product.png' })).toHaveAttribute(
      'src', 'https://cdn.example.com/product.thumbnail.webp',
    );
    expect(screen.queryByText('product.png')).not.toBeInTheDocument();
  });

  it('所有来源通过统一 id 删除，只有引用图片显示来源标记', () => {
    const onRemove = vi.fn();
    const quoted: ChatAttachment = {
      ...workspaceImage(), id: 'quote:https://cdn.example.com/product.png', source: 'quote',
    };
    render(<ChatAttachmentPreview attachments={[quoted]} onRemove={onRemove} />);

    expect(screen.getByText('引用')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '移除 product.png' }));
    expect(onRemove).toHaveBeenCalledWith(quoted.id);
  });

  it('普通文件保留文件名和大小信息', () => {
    const file: ChatAttachment = {
      id: 'workspace:docs/report.pdf', kind: 'file', source: 'workspace', status: 'ready',
      name: 'report.pdf', mimeType: 'application/pdf', size: 2048,
      originalUrl: 'https://cdn.example.com/report.pdf', workspacePath: 'docs/report.pdf',
    };
    render(<ChatAttachmentPreview attachments={[file]} onRemove={vi.fn()} />);

    expect(screen.getByText('report.pdf')).toBeInTheDocument();
    expect(screen.getByText('2.0KB')).toBeInTheDocument();
  });

  it('点击可用缩略图时使用原图打开统一预览', () => {
    render(<ChatAttachmentPreview attachments={[workspaceImage()]} onRemove={vi.fn()} />);

    fireEvent.click(screen.getByRole('img', { name: 'product.png' }));

    expect(previewMocks.open).toHaveBeenCalledWith([
      expect.objectContaining({ url: 'https://cdn.example.com/product.png' }),
    ], 0);
  });
});
