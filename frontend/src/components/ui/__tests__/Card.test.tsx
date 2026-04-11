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

  it('默认 variant 含 card-bg/card-border token 和主题圆角', () => {
    const { container } = render(<Card>x</Card>);
    const card = container.firstChild as HTMLElement;
    expect(card.className).toContain('c-card-bg');
    expect(card.className).toContain('c-card-border');
    expect(card.className).toContain('s-radius-card');
  });

  it('elevated variant 含 whisper shadow token', () => {
    const { container } = render(<Card variant="elevated">x</Card>);
    const card = container.firstChild as HTMLElement;
    expect(card.className).toContain('s-shadow-whisper');
  });

  it('interactive variant 含 cursor-pointer 和 hover shadow', () => {
    const { container } = render(<Card variant="interactive">x</Card>);
    const card = container.firstChild as HTMLElement;
    expect(card.className).toContain('cursor-pointer');
    expect(card.className).toContain('c-card-shadow-hover');
    // V3 改用 framer whileHover 提供 y 偏移，CSS 不再有 hover:-translate
    expect(card.className).not.toContain('hover:-translate-y');
  });

  it('glass variant 使用毛玻璃工具类', () => {
    const { container } = render(<Card variant="glass">x</Card>);
    const card = container.firstChild as HTMLElement;
    expect(card.className).toContain('glass');
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
