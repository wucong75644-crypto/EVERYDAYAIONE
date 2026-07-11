import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { MOCK_DETAIL_PLAN } from '../../../mocks/detailPageMocks';
import { GenerationProgress } from '../GenerationProgress';

describe('GenerationProgress', () => {
  it('按完成和失败条目计算整组进度', () => {
    const items = MOCK_DETAIL_PLAN.slice(0, 2).map((item, index) => ({ ...item, status: index === 0 ? 'completed' as const : 'generating' as const, previewUrl: index === 0 ? 'result.png' : null, error: null, refundedCredits: 0, versions: [] }));
    render(<GenerationProgress items={items} onRetry={vi.fn()} />);
    expect(screen.getByRole('heading', { name: '正在生成 1/2' })).toBeInTheDocument();
  });
});
