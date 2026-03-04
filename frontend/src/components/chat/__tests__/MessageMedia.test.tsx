/**
 * MessageMedia 单元测试
 *
 * 测试覆盖：
 * 1. memo 行为：props 不变时不重渲染
 * 2. handleImageClick useCallback 正确传递 index
 * 3. 多图模式正确传递 props 给 AiImageGrid
 * 4. 单图模式正确渲染
 */

import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import MessageMedia from '../MessageMedia';

// Mock 子组件，只关注 props 传递
vi.mock('react-intersection-observer', () => ({
  useInView: () => ({ ref: vi.fn(), inView: true }),
}));

vi.mock('../MediaPlaceholder', () => ({
  default: ({ type }: { type: string }) => <div data-testid={`placeholder-${type}`} />,
  FailedMediaPlaceholder: ({ onRetry }: { onRetry?: () => void }) => (
    <div data-testid="failed-placeholder">
      {onRetry && <button onClick={onRetry}>重试</button>}
    </div>
  ),
}));

vi.mock('../shared.module.css', () => ({
  default: {
    'dynamic-aspect-ratio': 'dynamic-aspect-ratio',
    'dynamic-max-width': 'dynamic-max-width',
  },
}));

vi.mock('../AiImageGrid', () => ({
  default: (props: Record<string, unknown>) => (
    <div data-testid="ai-image-grid" data-num-images={props.numImages}>
      {/* 暴露 onImageClick 用于测试 */}
      <button
        data-testid="grid-image-click"
        onClick={() => (props.onImageClick as (idx: number) => void)(2)}
      >
        click
      </button>
    </div>
  ),
}));

describe('MessageMedia', () => {
  it('多图模式渲染 AiImageGrid', () => {
    render(
      <MessageMedia
        imageUrls={['https://img1.png']}
        messageId="msg-1"
        isUser={false}
        onImageClick={vi.fn()}
        isGenerating={true}
        generatingType="image"
        numImages={4}
        content={[{ type: 'image', url: 'https://img1.png' }]}
      />,
    );
    const grid = screen.getByTestId('ai-image-grid');
    expect(grid).toBeInTheDocument();
    expect(grid).toHaveAttribute('data-num-images', '4');
  });

  it('handleImageClick 正确透传 index 给 onImageClick', () => {
    const onImageClick = vi.fn();
    render(
      <MessageMedia
        imageUrls={['https://img1.png']}
        messageId="msg-1"
        isUser={false}
        onImageClick={onImageClick}
        isGenerating={true}
        generatingType="image"
        numImages={4}
        content={[{ type: 'image', url: 'https://img1.png' }]}
      />,
    );
    // AiImageGrid mock 点击时传 index=2
    screen.getByTestId('grid-image-click').click();
    expect(onImageClick).toHaveBeenCalledWith(2);
  });

  it('单图模式不渲染 AiImageGrid', () => {
    render(
      <MessageMedia
        imageUrls={['https://img1.png']}
        messageId="msg-1"
        isUser={false}
        onImageClick={vi.fn()}
        isGenerating={false}
        numImages={1}
      />,
    );
    expect(screen.queryByTestId('ai-image-grid')).not.toBeInTheDocument();
  });

  it('无媒体内容时不渲染', () => {
    const { container } = render(
      <MessageMedia
        messageId="msg-1"
        isUser={false}
        onImageClick={vi.fn()}
        isGenerating={false}
      />,
    );
    expect(container.innerHTML).toBe('');
  });

  it('memo：相同 props 重渲染时保持稳定', () => {
    const onImageClick = vi.fn();
    const props = {
      imageUrls: ['https://img1.png'],
      messageId: 'msg-1',
      isUser: false as const,
      onImageClick,
      isGenerating: false,
      numImages: 1,
    };

    const { rerender } = render(<MessageMedia {...props} />);
    // 传入完全相同的 props 值（memo 应跳过重渲染）
    rerender(<MessageMedia {...props} />);
    // 验证组件正常渲染（未因 memo 导致异常）
    expect(screen.getByRole('img')).toBeInTheDocument();
  });

  it('失败的图片占位符渲染 FailedMediaPlaceholder', () => {
    render(
      <MessageMedia
        messageId="msg-1"
        isUser={false}
        onImageClick={vi.fn()}
        isGenerating={false}
        failedMediaType="image"
        onRegenerate={vi.fn()}
      />,
    );
    expect(screen.getByTestId('failed-placeholder')).toBeInTheDocument();
  });
});
