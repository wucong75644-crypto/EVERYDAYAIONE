/**
 * Input 组件测试
 */

import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { Input } from '../Input';

describe('Input', () => {
  it('渲染 label 并关联到 input', () => {
    render(<Input label="邮箱" />);
    const label = screen.getByText('邮箱');
    const input = screen.getByLabelText('邮箱');
    expect(label).toBeInTheDocument();
    expect(input).toBeInTheDocument();
    expect(label.tagName).toBe('LABEL');
  });

  it('支持 placeholder', () => {
    render(<Input placeholder="请输入邮箱" />);
    expect(screen.getByPlaceholderText('请输入邮箱')).toBeInTheDocument();
  });

  it('value/onChange 受控', () => {
    const handleChange = vi.fn();
    render(<Input value="hello" onChange={handleChange} />);
    const input = screen.getByDisplayValue('hello');
    fireEvent.change(input, { target: { value: 'world' } });
    expect(handleChange).toHaveBeenCalledOnce();
  });

  it('错误状态显示提示文字 + aria-invalid', () => {
    render(<Input label="邮箱" error="格式不正确" />);
    const input = screen.getByLabelText('邮箱');
    expect(input).toHaveAttribute('aria-invalid', 'true');
    expect(screen.getByRole('alert')).toHaveTextContent('格式不正确');
  });

  it('错误状态使用 error token border 样式', () => {
    render(<Input error="错误" />);
    const input = screen.getByRole('textbox');
    // V3：class 用 border-[var(--s-error)] 形式
    expect(input.className).toContain('s-error');
  });

  it('支持前置图标', () => {
    render(<Input icon={<span data-testid="icon">@</span>} />);
    expect(screen.getByTestId('icon')).toBeInTheDocument();
    // input 应该有左 padding 给图标留位
    expect(screen.getByRole('textbox').className).toContain('pl-9');
  });

  it('disabled 时不可输入', () => {
    render(<Input disabled />);
    expect(screen.getByRole('textbox')).toBeDisabled();
  });

  it('支持 type 属性（password/email 等）', () => {
    const { container } = render(<Input type="password" />);
    const input = container.querySelector('input');
    expect(input).toHaveAttribute('type', 'password');
  });

  it('未提供 label 时不渲染 label 元素', () => {
    const { container } = render(<Input />);
    expect(container.querySelector('label')).toBeNull();
  });

  it('未提供 error 时不渲染 alert', () => {
    render(<Input />);
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  it('支持 forwardRef', () => {
    const ref = { current: null as HTMLInputElement | null };
    render(<Input ref={ref} />);
    expect(ref.current).toBeInstanceOf(HTMLInputElement);
  });
});
