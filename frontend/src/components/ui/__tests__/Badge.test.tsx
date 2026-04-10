/**
 * Badge 组件测试
 */

import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { Badge } from '../Badge';

describe('Badge', () => {
  it('渲染 children 内容', () => {
    render(<Badge>新</Badge>);
    expect(screen.getByText('新')).toBeInTheDocument();
  });

  it('默认 variant 为 default', () => {
    render(<Badge>x</Badge>);
    expect(screen.getByText('x').className).toContain('text-text-secondary');
  });

  it('accent variant 使用品牌色', () => {
    render(<Badge variant="accent">x</Badge>);
    expect(screen.getByText('x').className).toContain('bg-accent-light');
    expect(screen.getByText('x').className).toContain('text-accent');
  });

  it('success variant', () => {
    render(<Badge variant="success">x</Badge>);
    expect(screen.getByText('x').className).toContain('bg-success-light');
  });

  it('error variant', () => {
    render(<Badge variant="error">x</Badge>);
    expect(screen.getByText('x').className).toContain('text-error');
  });

  it('warning variant', () => {
    render(<Badge variant="warning">x</Badge>);
    expect(screen.getByText('x').className).toContain('text-warning');
  });

  it('支持 size sm/md', () => {
    const { rerender } = render(<Badge size="sm">x</Badge>);
    expect(screen.getByText('x').className).toContain('text-xs');

    rerender(<Badge size="md">x</Badge>);
    expect(screen.getByText('x').className).toContain('text-sm');
  });

  it('全部使用 rounded-full', () => {
    render(<Badge>x</Badge>);
    expect(screen.getByText('x').className).toContain('rounded-full');
  });
});
