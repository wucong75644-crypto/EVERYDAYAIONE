import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { MOCK_DETAIL_PLAN } from '../../../mocks/detailPageMocks';
import { PlanReviewPanel } from '../PlanReviewPanel';

describe('PlanReviewPanel', () => {
  it('重新规划前要求二次确认', () => {
    const onReplan = vi.fn();
    render(<PlanReviewPanel plan={MOCK_DETAIL_PLAN} onChange={vi.fn()} onRemove={vi.fn()} onBack={vi.fn()} onReplan={onReplan} onConfirm={vi.fn()} />);
    fireEvent.click(screen.getByRole('button', { name: '重新规划' }));
    expect(screen.getByRole('alertdialog', { name: '确认重新规划' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '确认重新规划' }));
    expect(onReplan).toHaveBeenCalledOnce();
  });

  it('支持返回和确认生成', () => {
    const onBack = vi.fn();
    const onConfirm = vi.fn();
    render(<PlanReviewPanel plan={MOCK_DETAIL_PLAN} onChange={vi.fn()} onRemove={vi.fn()} onBack={onBack} onReplan={vi.fn()} onConfirm={onConfirm} />);
    fireEvent.click(screen.getByRole('button', { name: '返回修改需求' }));
    fireEvent.click(screen.getByRole('button', { name: '确认生成' }));
    expect(onBack).toHaveBeenCalledOnce();
    expect(onConfirm).toHaveBeenCalledOnce();
  });

  it('在确认操作附近展示积分错误', () => {
    render(<PlanReviewPanel plan={MOCK_DETAIL_PLAN} error="积分不足，请减少生成数量后重试" onChange={vi.fn()} onRemove={vi.fn()} onBack={vi.fn()} onReplan={vi.fn()} onConfirm={vi.fn()} />);
    expect(screen.getByRole('alert')).toHaveTextContent('积分不足');
  });
});
