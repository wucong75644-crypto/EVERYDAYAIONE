import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import WorkspaceAttachmentPreview from '../WorkspaceAttachmentPreview';

describe('WorkspaceAttachmentPreview', () => {
  it('为工作区图片显示缩略预览', () => {
    render(<WorkspaceAttachmentPreview files={[{
      name: 'product.png', workspace_path: '上传/product.png',
      cdn_url: 'https://cdn.example.com/product.png', mime_type: 'image/png', size: 10,
    }]} />);

    expect(screen.getByRole('img', { name: 'product.png' })).toHaveAttribute(
      'src', 'https://cdn.example.com/product.png',
    );
  });

  it('在 MIME 缺失时按扩展名显示图片', () => {
    render(<WorkspaceAttachmentPreview files={[{
      name: 'reference.webp', workspace_path: 'reference.webp',
      cdn_url: 'https://cdn.example.com/reference.webp', mime_type: null, size: 10,
    }]} />);

    expect(screen.getByRole('img', { name: 'reference.webp' })).toBeInTheDocument();
  });

  it('普通文件保持文件图标且不渲染图片', () => {
    render(<WorkspaceAttachmentPreview files={[{
      name: 'report.pdf', workspace_path: '上传/report.pdf',
      cdn_url: 'https://cdn.example.com/report.pdf', mime_type: 'application/pdf', size: 10,
    }]} />);

    expect(screen.queryByRole('img')).not.toBeInTheDocument();
    expect(screen.getByText('📄')).toBeInTheDocument();
  });

  it('移除时传递准确的工作区路径', () => {
    const onRemove = vi.fn();
    render(<WorkspaceAttachmentPreview files={[{
      name: 'product.jpg', workspace_path: '上传/product.jpg',
      cdn_url: 'https://cdn.example.com/product.jpg', mime_type: 'image/jpeg', size: 10,
    }]} onRemove={onRemove} />);

    fireEvent.click(screen.getByRole('button', { name: '移除 product.jpg' }));
    expect(onRemove).toHaveBeenCalledWith('上传/product.jpg');
  });
});
