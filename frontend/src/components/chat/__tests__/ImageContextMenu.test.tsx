/**
 * ImageContextMenu 单测
 *
 * 覆盖：
 * - 渲染三个菜单项（引用/复制/下载）
 * - 点击引用调用统一附件命令
 * - ESC 关闭菜单
 * - 点击外部关闭菜单
 */

import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import ImageContextMenu from '../media/ImageContextMenu';

const attachmentMocks = vi.hoisted(() => ({ addQuotedImage: vi.fn() }));
vi.mock('../attachments/ChatAttachmentContext', () => ({
  useChatAttachmentContext: () => attachmentMocks,
}));

describe('ImageContextMenu', () => {
  const defaultProps = {
    x: 100,
    y: 200,
    imageUrl: 'https://cdn.example.com/test.png',
    thumbnailUrl: 'https://cdn.example.com/test-thumb.png',
    messageId: 'msg-123',
    onClose: vi.fn(),
  };

  it('should render three menu items', () => {
    render(<ImageContextMenu {...defaultProps} />);

    expect(screen.getByText('引用')).toBeInTheDocument();
    expect(screen.getByText('复制')).toBeInTheDocument();
    expect(screen.getByText('下载')).toBeInTheDocument();
  });

  it('should add the quoted image through the attachment controller', () => {
    render(<ImageContextMenu {...defaultProps} />);
    fireEvent.click(screen.getByText('引用'));

    expect(attachmentMocks.addQuotedImage).toHaveBeenCalledWith({
      url: 'https://cdn.example.com/test.png',
      thumbnailUrl: 'https://cdn.example.com/test-thumb.png',
    });
    expect(defaultProps.onClose).toHaveBeenCalled();
  });

  it('should not add a quote when only thumbnail URL is provided', () => {
    attachmentMocks.addQuotedImage.mockClear();
    render(
      <ImageContextMenu
        {...defaultProps}
        imageUrl="https://cdn.everydayai.com.cn/workspace-thumbnails/a.w360.webp"
      />,
    );
    fireEvent.click(screen.getByText('引用'));

    expect(attachmentMocks.addQuotedImage).not.toHaveBeenCalled();
  });

  it('should call onClose when ESC is pressed', () => {
    const onClose = vi.fn();
    render(<ImageContextMenu {...defaultProps} onClose={onClose} />);

    fireEvent.keyDown(document, { key: 'Escape' });
    expect(onClose).toHaveBeenCalled();
  });

  it('should call onClose when clicking outside', () => {
    const onClose = vi.fn();
    render(<ImageContextMenu {...defaultProps} onClose={onClose} />);

    fireEvent.mouseDown(document.body);
    expect(onClose).toHaveBeenCalled();
  });

  it('should apply enter animation by default', () => {
    const { container } = render(<ImageContextMenu {...defaultProps} />);
    const menu = container.firstChild as HTMLElement;
    expect(menu.className).toContain('animate-dropdown-enter');
  });

  it('should apply exit animation when closing', () => {
    const { container } = render(<ImageContextMenu {...defaultProps} closing />);
    const menu = container.firstChild as HTMLElement;
    expect(menu.className).toContain('animate-dropdown-exit');
  });

  it('should position at specified coordinates', () => {
    const { container } = render(<ImageContextMenu {...defaultProps} />);
    const menu = container.firstChild as HTMLElement;
    expect(menu.style.left).toBe('100px');
    expect(menu.style.top).toBe('200px');
  });
});
