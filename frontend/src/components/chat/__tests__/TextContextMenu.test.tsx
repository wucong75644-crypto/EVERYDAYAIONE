/**
 * TextContextMenu 单测
 *
 * 覆盖：
 * - 渲染两个菜单项（引用/复制）
 * - 有选区时引用使用选区文字
 * - 无选区时引用使用全文
 * - 复制走 clipboard.writeText
 * - ESC / 点击外部关闭菜单
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import TextContextMenu from '../menus/TextContextMenu';

describe('TextContextMenu', () => {
  const baseProps = {
    x: 100,
    y: 200,
    fullText: '这是完整文字',
    selectedText: '',
    messageId: 'msg-123',
    onClose: vi.fn(),
  };

  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders 引用 and 复制', () => {
    render(<TextContextMenu {...baseProps} />);
    expect(screen.getByText('引用')).toBeInTheDocument();
    expect(screen.getByText('复制')).toBeInTheDocument();
  });

  it('dispatches chat:quote-text with full text when no selection', () => {
    const handler = vi.fn();
    window.addEventListener('chat:quote-text', handler);

    render(<TextContextMenu {...baseProps} />);
    fireEvent.click(screen.getByText('引用'));

    expect(handler).toHaveBeenCalledTimes(1);
    const detail = (handler.mock.calls[0][0] as CustomEvent).detail;
    expect(detail.text).toBe('这是完整文字');
    expect(detail.messageId).toBe('msg-123');
    expect(baseProps.onClose).toHaveBeenCalled();

    window.removeEventListener('chat:quote-text', handler);
  });

  it('dispatches chat:quote-text with selected text when selection present', () => {
    const handler = vi.fn();
    window.addEventListener('chat:quote-text', handler);

    render(<TextContextMenu {...baseProps} selectedText="部分选中" />);
    fireEvent.click(screen.getByText('引用'));

    const detail = (handler.mock.calls[0][0] as CustomEvent).detail;
    expect(detail.text).toBe('部分选中');

    window.removeEventListener('chat:quote-text', handler);
  });

  it('copies effective text via clipboard.writeText', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { clipboard: { writeText } });

    render(<TextContextMenu {...baseProps} selectedText="一段选区" />);
    fireEvent.click(screen.getByText('复制'));

    // 异步 microtask
    await Promise.resolve();
    expect(writeText).toHaveBeenCalledWith('一段选区');
  });

  it('closes on ESC', () => {
    const onClose = vi.fn();
    render(<TextContextMenu {...baseProps} onClose={onClose} />);
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(onClose).toHaveBeenCalled();
  });

  it('closes on outside click', () => {
    const onClose = vi.fn();
    render(<TextContextMenu {...baseProps} onClose={onClose} />);
    fireEvent.mouseDown(document.body);
    expect(onClose).toHaveBeenCalled();
  });
});
