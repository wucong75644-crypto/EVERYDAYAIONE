import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { ProductImageSection } from '../ProductImageSection';

describe('ProductImageSection', () => {
  it('显示两个上传板块和共享数量', () => {
    render(<ProductImageSection images={[]} error={null} onAdd={vi.fn()} onRemove={vi.fn()} />);
    expect(screen.getByText('产品图')).toBeInTheDocument();
    expect(screen.getByText('参考图')).toBeInTheDocument();
    expect(screen.getByText('0 / 9')).toBeInTheDocument();
  });

  it('选择文件时传递正确分类', () => {
    const onAdd = vi.fn();
    render(<ProductImageSection images={[]} error={null} onAdd={onAdd} onRemove={vi.fn()} />);
    const input = screen.getByLabelText('上传产品图');
    const file = new File(['x'], 'product.png', { type: 'image/png' });
    fireEvent.change(input, { target: { files: [file] } });
    expect(onAdd).toHaveBeenCalledWith('product', [file]);
  });

  it('参考图选择时传递 reference 分类', () => {
    const onAdd = vi.fn();
    render(<ProductImageSection images={[]} error={null} onAdd={onAdd} onRemove={vi.fn()} />);
    const input = screen.getByLabelText('上传参考图');
    const file = new File(['x'], 'reference.webp', { type: 'image/webp' });
    fireEvent.change(input, { target: { files: [file] } });
    expect(onAdd).toHaveBeenCalledWith('reference', [file]);
  });

  it('显示错误并允许删除已有图片', () => {
    const onRemove = vi.fn();
    const file = new File(['x'], 'product.png', { type: 'image/png' });
    render(
      <ProductImageSection
        images={[{ id: 'img-1', category: 'product', file, previewUrl: 'blob:preview', error: null }]}
        error="格式错误"
        onAdd={vi.fn()}
        onRemove={onRemove}
      />,
    );
    expect(screen.getByRole('alert')).toHaveTextContent('格式错误');
    fireEvent.click(screen.getByLabelText('删除 product.png'));
    expect(onRemove).toHaveBeenCalledWith('img-1');
  });

  it('禁用时不能选择或删除图片', () => {
    const file = new File(['x'], 'product.png', { type: 'image/png' });
    render(
      <ProductImageSection
        images={[{ id: 'img-1', category: 'product', file, previewUrl: 'blob:preview', error: null }]}
        error={null}
        disabled
        onAdd={vi.fn()}
        onRemove={vi.fn()}
      />,
    );
    expect(screen.getByLabelText('上传产品图')).toBeDisabled();
    expect(screen.queryByLabelText('删除 product.png')).not.toBeInTheDocument();
  });
});
