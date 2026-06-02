/**
 * UploadMenu 单测（V4 合并版）
 *
 * 验证：
 * - visible=false 时不渲染
 * - 单一「上传文件」按钮（不再有「上传图片/文档/截图/工作区」4 项）
 * - 内部 input 接受多文件
 * - 文件选择后 onFilesSelected 被调用，参数是 File[]
 * - 选择后 onClose 被调用（自动关菜单）
 */

import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import UploadMenu from '../UploadMenu';

function setup(visible = true) {
  const onFilesSelected = vi.fn();
  const onClose = vi.fn();
  const utils = render(
    <UploadMenu
      visible={visible}
      onFilesSelected={onFilesSelected}
      onClose={onClose}
    />,
  );
  return { ...utils, onFilesSelected, onClose };
}

describe('UploadMenu (合并版)', () => {
  it('visible=false 时不渲染任何内容', () => {
    const { container } = setup(false);
    expect(container.firstChild).toBeNull();
  });

  it('visible=true 时渲染单一「上传文件」按钮', () => {
    setup(true);
    expect(screen.getByText('上传文件')).toBeInTheDocument();
    // 不再有旧入口
    expect(screen.queryByText('上传图片')).toBeNull();
    expect(screen.queryByText('上传文档')).toBeNull();
    expect(screen.queryByText('上传到工作区')).toBeNull();
    expect(screen.queryByText('屏幕截图')).toBeNull();
  });

  it('帮助文字提及支持的多种格式', () => {
    setup(true);
    const desc = screen.getByText(/图片.*PDF.*Excel/);
    expect(desc).toBeInTheDocument();
  });

  it('input 是 multiple file picker', () => {
    setup(true);
    const input = screen.getByLabelText('上传文件') as HTMLInputElement;
    expect(input.type).toBe('file');
    expect(input.multiple).toBe(true);
    expect(input.hidden || input.className.includes('hidden')).toBeTruthy();
  });

  it('accept 涵盖图片+文档+数据格式', () => {
    setup(true);
    const input = screen.getByLabelText('上传文件') as HTMLInputElement;
    const accept = input.accept;
    // 图片
    expect(accept).toMatch(/\.png/);
    expect(accept).toMatch(/\.jpg/);
    // 文档
    expect(accept).toMatch(/\.pdf/);
    expect(accept).toMatch(/\.docx/);
    // 数据
    expect(accept).toMatch(/\.xlsx/);
    expect(accept).toMatch(/\.csv/);
    // 安全：不含 svg（XSS 风险，从白名单移除）
    expect(accept).not.toMatch(/\.svg/);
  });

  it('选择文件后 onFilesSelected 被调用且收到 File[]', () => {
    const { onFilesSelected, onClose } = setup(true);
    const input = screen.getByLabelText('上传文件') as HTMLInputElement;

    const f1 = new File(['data'], 'a.png', { type: 'image/png' });
    const f2 = new File(['data'], 'b.pdf', { type: 'application/pdf' });

    // 模拟选文件
    Object.defineProperty(input, 'files', { value: [f1, f2], writable: false });
    fireEvent.change(input);

    expect(onFilesSelected).toHaveBeenCalledTimes(1);
    const calledWith = onFilesSelected.mock.calls[0][0] as File[];
    expect(calledWith).toHaveLength(2);
    expect(calledWith[0].name).toBe('a.png');
    expect(calledWith[1].name).toBe('b.pdf');

    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('未选择文件（空 FileList）不触发回调', () => {
    const { onFilesSelected, onClose } = setup(true);
    const input = screen.getByLabelText('上传文件') as HTMLInputElement;

    Object.defineProperty(input, 'files', { value: [], writable: false });
    fireEvent.change(input);

    expect(onFilesSelected).not.toHaveBeenCalled();
    expect(onClose).not.toHaveBeenCalled();
  });
});
