import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { MOCK_DETAIL_PLAN } from '../../../mocks/detailPageMocks';
import { GenerationCard } from '../GenerationCard';

describe('GenerationCard', () => {
  it('失败时显示原因、退款和重试', () => {
    const onRetry = vi.fn();
    render(<GenerationCard item={{ ...MOCK_DETAIL_PLAN[0], status: 'failed', previewUrl: null, error: '生成超时', refundedCredits: 10, versions: [] }} onRetry={onRetry} />);
    expect(screen.getByRole('alert')).toHaveTextContent('已退还 10 积分');
    fireEvent.click(screen.getByRole('button', { name: '重试该张' }));
    expect(onRetry).toHaveBeenCalledOnce();
  });

  it('完成时展示图片和下载入口', () => {
    render(<GenerationCard item={{ ...MOCK_DETAIL_PLAN[0], status: 'completed', previewUrl: 'result.png', error: null, refundedCredits: 0, versions: ['result.png'] }} onRetry={vi.fn()} />);
    expect(screen.getByRole('img', { name: '钩子主图生成结果' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: '下载' })).toHaveAttribute('download', '钩子主图.svg');
  });
});
