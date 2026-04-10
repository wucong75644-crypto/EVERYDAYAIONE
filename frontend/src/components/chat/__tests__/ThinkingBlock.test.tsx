/**
 * ThinkingBlock 组件单元测试
 *
 * 覆盖：默认折叠、展开/折叠切换、思考中动画、完成时长显示、空内容不渲染
 */

import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import ThinkingBlock from '../message/ThinkingBlock';

describe('ThinkingBlock', () => {
  it('should not render when content is empty and not thinking', () => {
    const { container } = render(
      <ThinkingBlock content="" isThinking={false} />,
    );
    expect(container.innerHTML).toBe('');
  });

  it('should render when isThinking even with empty content', () => {
    render(<ThinkingBlock content="" isThinking={true} />);
    expect(screen.getByText('思考中')).toBeDefined();
  });

  it('should show "已深度思考" when thinking is complete', () => {
    render(<ThinkingBlock content="推理过程" isThinking={false} />);
    expect(screen.getByText('已深度思考')).toBeDefined();
  });

  it('should be collapsed by default', () => {
    render(<ThinkingBlock content="推理内容" isThinking={false} />);
    // 内容不应该可见（默认折叠）
    expect(screen.queryByText('推理内容')).toBeNull();
  });

  it('should expand content on click', () => {
    render(<ThinkingBlock content="展开后可见" isThinking={false} />);

    const button = screen.getByRole('button');
    fireEvent.click(button);

    expect(screen.getByText('展开后可见')).toBeDefined();
  });

  it('should collapse content on second click', () => {
    render(<ThinkingBlock content="可折叠内容" isThinking={false} />);

    const button = screen.getByRole('button');
    fireEvent.click(button); // expand
    fireEvent.click(button); // collapse

    expect(screen.queryByText('可折叠内容')).toBeNull();
  });

  it('should show duration when thinkingStartTime is provided', () => {
    // 固定 Date.now 让 duration 可预测
    const now = 1710000000000;
    vi.spyOn(Date, 'now').mockReturnValue(now);

    render(
      <ThinkingBlock
        content="思考完成"
        isThinking={false}
        thinkingStartTime={now - 5000} // 5 秒前
      />,
    );

    expect(screen.getByText(/5秒/)).toBeDefined();
    vi.restoreAllMocks();
  });

  it('should not show duration when still thinking', () => {
    render(
      <ThinkingBlock
        content="进行中"
        isThinking={true}
        thinkingStartTime={Date.now() - 3000}
      />,
    );

    // 思考中不显示时长
    expect(screen.queryByText(/秒/)).toBeNull();
  });

  it('should show thinking dots animation when isThinking', () => {
    const { container } = render(
      <ThinkingBlock content="" isThinking={true} />,
    );
    const dots = container.querySelectorAll('.thinking-dot');
    expect(dots.length).toBe(3);
  });
});
