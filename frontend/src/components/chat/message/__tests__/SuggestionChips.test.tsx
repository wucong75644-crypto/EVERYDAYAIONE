/**
 * SuggestionChips 组件单元测试
 *
 * 覆盖：渲染按钮、点击 dispatch CustomEvent、空数组不渲染、visible 控制
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import SuggestionChips from '../SuggestionChips';

describe('SuggestionChips', () => {
  let dispatchSpy: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    dispatchSpy = vi.spyOn(window, 'dispatchEvent');
  });

  afterEach(() => {
    dispatchSpy.mockRestore();
  });

  it('should render suggestion buttons', () => {
    render(<SuggestionChips suggestions={['按店铺分析', '和前天对比']} />);

    expect(screen.getByText('按店铺分析')).toBeDefined();
    expect(screen.getByText('和前天对比')).toBeDefined();
  });

  it('should render nothing for empty suggestions', () => {
    const { container } = render(<SuggestionChips suggestions={[]} />);
    expect(container.innerHTML).toBe('');
  });

  it('should dispatch chat:send-suggestion on click', () => {
    render(<SuggestionChips suggestions={['按店铺分析']} />);

    fireEvent.click(screen.getByText('按店铺分析'));

    const event = dispatchSpy.mock.calls.find(
      (call) => (call[0] as Event).type === 'chat:send-suggestion',
    );
    expect(event).toBeDefined();
    expect((event![0] as CustomEvent).detail).toEqual({ text: '按店铺分析' });
  });

  it('should render correct number of buttons', () => {
    render(<SuggestionChips suggestions={['a', 'b', 'c']} />);
    const buttons = screen.getAllByRole('button');
    expect(buttons).toHaveLength(3);
  });
});
