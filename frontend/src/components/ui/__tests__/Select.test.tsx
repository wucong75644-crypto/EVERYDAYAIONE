import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { Select } from '../Select';

describe('Select', () => {
  it('锚定显示选项并返回新值', () => {
    const onChange = vi.fn();
    render(<Select ariaLabel="数量" value="1" options={[{ value: '1', label: '1 张' }, { value: '2', label: '2 张' }]} onChange={onChange} />);
    fireEvent.keyDown(screen.getByRole('button', { name: '数量' }), { key: 'ArrowDown' });
    fireEvent.click(screen.getByText('2 张'));
    expect(onChange).toHaveBeenCalledWith('2');
  });

  it('禁用时不能展开', () => {
    render(<Select ariaLabel="数量" disabled value="1" options={[{ value: '1', label: '1 张' }]} onChange={vi.fn()} />);
    expect(screen.getByRole('button', { name: '数量' })).toBeDisabled();
  });
});
