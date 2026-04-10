/**
 * Card 组件测试
 */

import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { Card } from '../Card';

describe('Card', () => {
  it('渲染 children 内容', () => {
    render(<Card>卡片内容</Card>);
    expect(screen.getByText('卡片内容')).toBeInTheDocument();
  });

  it('默认 variant 应用基础样式', () => {
    const { container } = render(<Card>x</Card>);
    const card = container.firstChild as HTMLElement;
    expect(card.className).toContain('bg-surface-card');
    expect(card.className).toContain('border-border-default');
    expect(card.className).toContain('rounded-xl');
  });

  it('elevated variant 添加阴影', () => {
    const { container } = render(<Card variant="elevated">x</Card>);
    const card = container.firstChild as HTMLElement;
    expect(card.className).toContain('shadow-md');
  });

  it('interactive variant 包含 hover 样式', () => {
    const { container } = render(<Card variant="interactive">x</Card>);
    const card = container.firstChild as HTMLElement;
    expect(card.className).toContain('cursor-pointer');
    expect(card.className).toContain('hover:shadow-md');
    expect(card.className).toContain('hover:-translate-y-0.5');
  });

  it('支持 padding 控制', () => {
    const { container, rerender } = render(<Card padding="none">x</Card>);
    let card = container.firstChild as HTMLElement;
    expect(card.className).not.toContain('p-3');
    expect(card.className).not.toContain('p-4');

    rerender(<Card padding="sm">x</Card>);
    card = container.firstChild as HTMLElement;
    expect(card.className).toContain('p-3');

    rerender(<Card padding="md">x</Card>);
    card = container.firstChild as HTMLElement;
    expect(card.className).toContain('p-4');

    rerender(<Card padding="lg">x</Card>);
    card = container.firstChild as HTMLElement;
    expect(card.className).toContain('p-6');
  });

  it('interactive 支持点击', () => {
    const handleClick = vi.fn();
    render(
      <Card variant="interactive" onClick={handleClick}>
        点我
      </Card>,
    );
    fireEvent.click(screen.getByText('点我'));
    expect(handleClick).toHaveBeenCalledOnce();
  });

  it('支持自定义 className 合并', () => {
    const { container } = render(<Card className="custom">x</Card>);
    const card = container.firstChild as HTMLElement;
    expect(card.className).toContain('custom');
  });
});
