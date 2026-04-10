/**
 * Button 组件测试
 */

import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { Button } from '../Button';

describe('Button', () => {
  it('渲染 children 内容', () => {
    render(<Button>点击我</Button>);
    expect(screen.getByRole('button', { name: '点击我' })).toBeInTheDocument();
  });

  it('默认 variant 为 accent', () => {
    render(<Button>btn</Button>);
    const btn = screen.getByRole('button');
    expect(btn.className).toContain('bg-accent');
  });

  it('支持 secondary variant', () => {
    render(<Button variant="secondary">btn</Button>);
    const btn = screen.getByRole('button');
    expect(btn.className).toContain('bg-surface-card');
  });

  it('支持 ghost variant', () => {
    render(<Button variant="ghost">btn</Button>);
    const btn = screen.getByRole('button');
    expect(btn.className).toContain('bg-transparent');
    expect(btn.className).toContain('text-text-secondary');
  });

  it('支持 danger variant', () => {
    render(<Button variant="danger">btn</Button>);
    const btn = screen.getByRole('button');
    expect(btn.className).toContain('text-error');
  });

  it('支持 size sm/md/lg', () => {
    const { rerender } = render(<Button size="sm">btn</Button>);
    expect(screen.getByRole('button').className).toContain('px-3');
    expect(screen.getByRole('button').className).toContain('py-1.5');

    rerender(<Button size="md">btn</Button>);
    expect(screen.getByRole('button').className).toContain('px-4');
    expect(screen.getByRole('button').className).toContain('py-2');

    rerender(<Button size="lg">btn</Button>);
    expect(screen.getByRole('button').className).toContain('px-5');
    expect(screen.getByRole('button').className).toContain('py-2.5');
  });

  it('loading 状态显示 spinner 并禁用点击', () => {
    const handleClick = vi.fn();
    render(
      <Button loading onClick={handleClick}>
        提交
      </Button>,
    );
    const btn = screen.getByRole('button');
    expect(btn).toBeDisabled();
    fireEvent.click(btn);
    expect(handleClick).not.toHaveBeenCalled();
  });

  it('disabled 状态禁用点击', () => {
    const handleClick = vi.fn();
    render(
      <Button disabled onClick={handleClick}>
        提交
      </Button>,
    );
    fireEvent.click(screen.getByRole('button'));
    expect(handleClick).not.toHaveBeenCalled();
  });

  it('正常点击触发 onClick', () => {
    const handleClick = vi.fn();
    render(<Button onClick={handleClick}>点击</Button>);
    fireEvent.click(screen.getByRole('button'));
    expect(handleClick).toHaveBeenCalledOnce();
  });

  it('支持 icon 前置图标', () => {
    render(<Button icon={<span data-testid="icon">★</span>}>btn</Button>);
    expect(screen.getByTestId('icon')).toBeInTheDocument();
  });

  it('loading 时不显示 icon', () => {
    render(
      <Button loading icon={<span data-testid="icon">★</span>}>
        btn
      </Button>,
    );
    expect(screen.queryByTestId('icon')).not.toBeInTheDocument();
  });

  it('fullWidth 添加 w-full class', () => {
    render(<Button fullWidth>btn</Button>);
    expect(screen.getByRole('button').className).toContain('w-full');
  });

  it('保留 focus-visible 状态而非 focus', () => {
    render(<Button>btn</Button>);
    const btn = screen.getByRole('button');
    expect(btn.className).toContain('focus-visible:ring-2');
  });

  it('保留 active 微缩反馈', () => {
    render(<Button>btn</Button>);
    expect(screen.getByRole('button').className).toContain('active:scale-[0.98]');
  });

  it('支持 forwardRef', () => {
    const ref = { current: null as HTMLButtonElement | null };
    render(<Button ref={ref}>btn</Button>);
    expect(ref.current).toBeInstanceOf(HTMLButtonElement);
  });

  it('支持自定义 className 合并', () => {
    render(<Button className="custom-class">btn</Button>);
    expect(screen.getByRole('button').className).toContain('custom-class');
  });
});
