/**
 * Tooltip primitive 测试
 *
 * Radix Tooltip 在 jsdom 里有个坑：它用 pointer events + hover 检测
 * jsdom 不完全支持。这里只测基础渲染 + disabled 逻辑，完整交互留给 E2E。
 */

import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { Tooltip } from '../Tooltip';

describe('Tooltip', () => {
  it('渲染 children 作为 trigger', () => {
    render(
      <Tooltip content="提示文字">
        <button>按钮</button>
      </Tooltip>,
    );
    expect(screen.getByText('按钮')).toBeInTheDocument();
  });

  it('disabled=true 时直接渲染 children，不包 Tooltip 容器', () => {
    render(
      <Tooltip content="提示" disabled>
        <button data-testid="child">按钮</button>
      </Tooltip>,
    );
    expect(screen.getByTestId('child')).toBeInTheDocument();
    // disabled 时 trigger 不带 Radix 的 data-state 属性
    expect(screen.getByTestId('child')).not.toHaveAttribute('data-state');
  });

  it('默认情况下 tooltip 内容初始不显示（delayDuration 未触发）', () => {
    render(
      <Tooltip content="隐藏的提示">
        <button>按钮</button>
      </Tooltip>,
    );
    expect(screen.queryByText('隐藏的提示')).not.toBeInTheDocument();
  });
});
