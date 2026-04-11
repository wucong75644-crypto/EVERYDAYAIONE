/**
 * motion/ 原语测试
 *
 * 因为 framer-motion 在测试环境下 skipAnimations = true，
 * 动效逻辑无法验证，这里只测试"渲染 + children 正确 + prop 接收"。
 */

import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import {
  Reveal,
  PageTransition,
  Stagger,
  StaggerItem,
  MagneticButton,
  LayoutTransition,
} from '..';

describe('Reveal', () => {
  it('渲染 children', () => {
    render(<Reveal>Hello</Reveal>);
    expect(screen.getByText('Hello')).toBeInTheDocument();
  });

  it('接收 delay / y / amount / once / className prop', () => {
    render(
      <Reveal delay={0.2} y={32} amount={0.5} once={false} className="reveal">
        内容
      </Reveal>,
    );
    expect(screen.getByText('内容')).toBeInTheDocument();
  });
});

describe('PageTransition', () => {
  it('渲染 children', () => {
    render(<PageTransition>页面</PageTransition>);
    expect(screen.getByText('页面')).toBeInTheDocument();
  });

  it('接收 y prop', () => {
    render(<PageTransition y={16}>内容</PageTransition>);
    expect(screen.getByText('内容')).toBeInTheDocument();
  });
});

describe('Stagger', () => {
  it('渲染所有子元素', () => {
    render(
      <Stagger>
        <StaggerItem>一</StaggerItem>
        <StaggerItem>二</StaggerItem>
        <StaggerItem>三</StaggerItem>
      </Stagger>,
    );
    expect(screen.getByText('一')).toBeInTheDocument();
    expect(screen.getByText('二')).toBeInTheDocument();
    expect(screen.getByText('三')).toBeInTheDocument();
  });

  it('as="ul" 渲染为 ul 标签', () => {
    const { container } = render(
      <Stagger as="ul">
        <StaggerItem>项</StaggerItem>
      </Stagger>,
    );
    expect(container.querySelector('ul')).toBeInTheDocument();
  });

  it('as="section" 渲染为 section 标签', () => {
    const { container } = render(
      <Stagger as="section">
        <StaggerItem>项</StaggerItem>
      </Stagger>,
    );
    expect(container.querySelector('section')).toBeInTheDocument();
  });

  it('自定义 staggerDelay 和 initialDelay', () => {
    render(
      <Stagger staggerDelay={0.1} initialDelay={0.05}>
        <StaggerItem>x</StaggerItem>
      </Stagger>,
    );
    expect(screen.getByText('x')).toBeInTheDocument();
  });
});

describe('MagneticButton', () => {
  it('渲染 children', () => {
    render(
      <MagneticButton>
        <button>吸附按钮</button>
      </MagneticButton>,
    );
    expect(screen.getByText('吸附按钮')).toBeInTheDocument();
  });

  it('接收 strength prop', () => {
    render(
      <MagneticButton strength={0.5}>
        <button>test</button>
      </MagneticButton>,
    );
    expect(screen.getByText('test')).toBeInTheDocument();
  });
});

describe('LayoutTransition', () => {
  it('默认渲染为 div', () => {
    const { container } = render(
      <LayoutTransition>content</LayoutTransition>,
    );
    expect(container.querySelector('div')).toBeInTheDocument();
    expect(screen.getByText('content')).toBeInTheDocument();
  });

  it('as="li" 渲染为 li', () => {
    const { container } = render(
      <LayoutTransition as="li">li item</LayoutTransition>,
    );
    expect(container.querySelector('li')).toBeInTheDocument();
  });

  it('as="article" 渲染为 article', () => {
    const { container } = render(
      <LayoutTransition as="article">article</LayoutTransition>,
    );
    expect(container.querySelector('article')).toBeInTheDocument();
  });

  it('接收 layoutId 用于 Magic Move', () => {
    render(
      <LayoutTransition layoutId="shared-id">shared</LayoutTransition>,
    );
    expect(screen.getByText('shared')).toBeInTheDocument();
  });

  it('接收 spring=false 走默认 duration 过渡', () => {
    render(
      <LayoutTransition spring={false}>content</LayoutTransition>,
    );
    expect(screen.getByText('content')).toBeInTheDocument();
  });
});
