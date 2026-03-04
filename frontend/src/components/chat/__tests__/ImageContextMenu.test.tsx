/**
 * ImageContextMenu 单测
 *
 * 覆盖：
 * - 渲染三个菜单项（引用/复制/下载）
 * - 点击引用触发 chat:quote-image 自定义事件
 * - ESC 关闭菜单
 * - 点击外部关闭菜单
 */

import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import ImageContextMenu from '../ImageContextMenu';

describe('ImageContextMenu', () => {
  const defaultProps = {
    x: 100,
    y: 200,
    imageUrl: 'https://cdn.example.com/test.png',
    messageId: 'msg-123',
    onClose: vi.fn(),
  };

  it('should render three menu items', () => {
    render(<ImageContextMenu {...defaultProps} />);

    expect(screen.getByText('引用')).toBeInTheDocument();
    expect(screen.getByText('复制')).toBeInTheDocument();
    expect(screen.getByText('下载')).toBeInTheDocument();
  });

  it('should dispatch chat:quote-image event on quote click', () => {
    const eventHandler = vi.fn();
    window.addEventListener('chat:quote-image', eventHandler);

    render(<ImageContextMenu {...defaultProps} />);
    fireEvent.click(screen.getByText('引用'));

    expect(eventHandler).toHaveBeenCalledTimes(1);
    const detail = (eventHandler.mock.calls[0][0] as CustomEvent).detail;
    expect(detail.url).toBe('https://cdn.example.com/test.png');
    expect(detail.messageId).toBe('msg-123');
    expect(defaultProps.onClose).toHaveBeenCalled();

    window.removeEventListener('chat:quote-image', eventHandler);
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

  it('should apply slideDown animation by default', () => {
    const { container } = render(<ImageContextMenu {...defaultProps} />);
    const menu = container.firstChild as HTMLElement;
    expect(menu.className).toContain('animate-slideDown');
  });

  it('should apply slideUp animation when closing', () => {
    const { container } = render(<ImageContextMenu {...defaultProps} closing />);
    const menu = container.firstChild as HTMLElement;
    expect(menu.className).toContain('animate-slideUp');
  });

  it('should position at specified coordinates', () => {
    const { container } = render(<ImageContextMenu {...defaultProps} />);
    const menu = container.firstChild as HTMLElement;
    expect(menu.style.left).toBe('100px');
    expect(menu.style.top).toBe('200px');
  });
});
