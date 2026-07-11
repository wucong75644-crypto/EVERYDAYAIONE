import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { MOCK_DETAIL_PLAN } from '../../../mocks/detailPageMocks';
import { ResultGallery } from '../ResultGallery';

describe('ResultGallery', () => {
  it('展示成功失败统计并支持再次制作和返回方案', () => {
    const onRestart = vi.fn();
    const onBack = vi.fn();
    const items = MOCK_DETAIL_PLAN.slice(0, 2).map((item, index) => ({ ...item, status: index === 0 ? 'completed' as const : 'failed' as const, previewUrl: index === 0 ? 'result.png' : null, error: index === 1 ? '失败' : null, refundedCredits: index === 1 ? 10 : 0, versions: [] }));
    render(<ResultGallery items={items} onRetry={vi.fn()} onRestart={onRestart} onBack={onBack} />);
    expect(screen.getByText('成功 1 张，失败 1 张')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '再次制作' }));
    fireEvent.click(screen.getByRole('button', { name: '返回修改方案' }));
    expect(onRestart).toHaveBeenCalledOnce();
    expect(onBack).toHaveBeenCalledOnce();
  });
});
