import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { AnalyzingPanel } from '../AnalyzingPanel';

describe('AnalyzingPanel', () => {
  it('展示当前分析阶段并允许取消', () => {
    const onCancel = vi.fn();
    render(<AnalyzingPanel stage={1} onCancel={onCancel} />);
    expect(screen.getByText('分析视觉特征')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '取消分析' }));
    expect(onCancel).toHaveBeenCalledOnce();
  });
});
