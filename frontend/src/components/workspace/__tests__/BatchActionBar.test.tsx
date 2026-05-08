/**
 * BatchActionBar 组件单测
 *
 * 覆盖：0 选中不渲染、显示选中数、点击删除/取消回调。
 */

import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import BatchActionBar from '../BatchActionBar';

describe('BatchActionBar', () => {
  it('should not render when selectedCount is 0', () => {
    const { container } = render(
      <BatchActionBar selectedCount={0} onDelete={vi.fn()} onClear={vi.fn()} />,
    );
    expect(container.innerHTML).toBe('');
  });

  it('should display selected count', () => {
    render(
      <BatchActionBar selectedCount={3} onDelete={vi.fn()} onClear={vi.fn()} />,
    );
    expect(screen.getByText('3')).toBeTruthy();
    expect(screen.getByText(/已选中/)).toBeTruthy();
  });

  it('should call onDelete when delete button clicked', () => {
    const onDelete = vi.fn();
    render(
      <BatchActionBar selectedCount={2} onDelete={onDelete} onClear={vi.fn()} />,
    );
    fireEvent.click(screen.getByText('删除'));
    expect(onDelete).toHaveBeenCalledOnce();
  });

  it('should call onClear when cancel button clicked', () => {
    const onClear = vi.fn();
    render(
      <BatchActionBar selectedCount={2} onDelete={vi.fn()} onClear={onClear} />,
    );
    fireEvent.click(screen.getByText('取消选择'));
    expect(onClear).toHaveBeenCalledOnce();
  });
});
