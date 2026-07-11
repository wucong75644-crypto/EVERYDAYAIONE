import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { MOCK_DETAIL_PLAN } from '../../../mocks/detailPageMocks';
import { PlanCard } from '../PlanCard';

describe('PlanCard', () => {
  it('编辑文案和高级提示词', () => {
    const onChange = vi.fn();
    render(<PlanCard item={MOCK_DETAIL_PLAN[0]} canRemove onChange={onChange} onRemove={vi.fn()} />);
    fireEvent.change(screen.getByLabelText('标题'), { target: { value: '新品标题' } });
    expect(onChange).toHaveBeenCalledWith({ title: '新品标题' });
    fireEvent.click(screen.getByRole('button', { name: '高级提示词' }));
    fireEvent.change(screen.getByLabelText('高级提示词'), { target: { value: 'new prompt' } });
    expect(onChange).toHaveBeenCalledWith({ prompt: 'new prompt' });
  });

  it('最后一张规划禁用删除', () => {
    render(<PlanCard item={MOCK_DETAIL_PLAN[0]} canRemove={false} onChange={vi.fn()} onRemove={vi.fn()} />);
    expect(screen.getByRole('button', { name: '删除钩子主图' })).toBeDisabled();
  });
});
