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

  it('默认 variant 含 text-secondary token class', () => {
    render(<Badge>x</Badge>);
    expect(screen.getByText('x').className).toContain('s-text-secondary');
  });

  it('accent variant 含 accent token class', () => {
    render(<Badge variant="accent">x</Badge>);
    const el = screen.getByText('x');
    expect(el.className).toContain('s-accent');
  });

  it('success variant 含 success token class', () => {
    render(<Badge variant="success">x</Badge>);
    expect(screen.getByText('x').className).toContain('s-success');
  });

  it('error variant 含 error token class', () => {
    render(<Badge variant="error">x</Badge>);
    expect(screen.getByText('x').className).toContain('s-error');
  });

  it('warning variant 含 warning token class', () => {
    render(<Badge variant="warning">x</Badge>);
    expect(screen.getByText('x').className).toContain('s-warning');
  });

  it('pulse prop 渲染 framer motion span（动画元素）', () => {
    render(<Badge pulse>在线</Badge>);
    expect(screen.getByText('在线')).toBeInTheDocument();
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
