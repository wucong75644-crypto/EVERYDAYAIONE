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
import { fireEvent, render, screen } from '@testing-library/react';
import MessageMedia from '../message/MessageMedia';

// Mock 子组件，只关注 props 传递
vi.mock('react-intersection-observer', () => ({
  useInView: () => ({ ref: vi.fn(), inView: true }),
}));

vi.mock('../media/MediaPlaceholder', () => ({
  default: ({ type }: { type: string }) => <div data-testid={`placeholder-${type}`} />,
  FailedMediaPlaceholder: ({ onRetry, errorMessage, errorCode }: { onRetry?: () => void; errorMessage?: string; errorCode?: string }) => (
    <div data-testid="failed-placeholder" data-error-code={errorCode}>
      {errorMessage && <span>{errorMessage}</span>}
      {onRetry && <button onClick={onRetry}>重试</button>}
    </div>
  ),
}));

vi.mock('../menus/shared.module.css', () => ({
  default: {
    'dynamic-aspect-ratio': 'dynamic-aspect-ratio',
    'dynamic-max-width': 'dynamic-max-width',
  },
}));

vi.mock('../media/AiImageGrid', () => ({
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
        imageAssets={[{ originalUrl: 'https://img1.png' }]}
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
        imageAssets={[{ originalUrl: 'https://img1.png' }]}
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
        imageAssets={[{ originalUrl: 'https://img1.png' }]}
        messageId="msg-1"
        isUser={false}
        onImageClick={vi.fn()}
        isGenerating={false}
        numImages={1}
      />,
    );
    expect(screen.queryByTestId('ai-image-grid')).not.toBeInTheDocument();
  });

  it('用户图片缩略图加载失败时回退到原图', () => {
    render(
      <MessageMedia
        imageAssets={[{
          originalUrl: 'https://cdn.example.com/original.png',
          thumbnailUrl: 'https://cdn.example.com/thumbnail.webp',
        }]}
        messageId="msg-user-image"
        isUser
        onImageClick={vi.fn()}
      />,
    );

    const image = screen.getByRole('img');
    expect(image).toHaveAttribute('src', 'https://cdn.example.com/thumbnail.webp');
    fireEvent.error(image);
    expect(screen.getByRole('img')).toHaveAttribute('src', 'https://cdn.example.com/original.png');
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
      imageAssets: [{ originalUrl: 'https://img1.png' }],
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
        content={[{ type: 'image', url: null, failed: true, error: '图片生成超时' }]}
      />,
    );
    expect(screen.getByTestId('failed-placeholder')).toBeInTheDocument();
    expect(screen.getByText('图片生成超时')).toBeInTheDocument();
  });

  it('单图积分不足时传递结构化错误码', () => {
    render(
      <MessageMedia
        messageId="msg-credits"
        isUser={false}
        onImageClick={vi.fn()}
        failedMediaType="image"
        content={[{
          type: 'image', url: null, failed: true,
          error: 'provider raw error', error_code: 'INSUFFICIENT_CREDITS',
        }]}
      />,
    );

    expect(screen.getByTestId('failed-placeholder')).toHaveAttribute(
      'data-error-code',
      'INSUFFICIENT_CREDITS',
    );
  });

  it('多图全部失败时仍按数量渲染 AiImageGrid', () => {
    render(
      <MessageMedia
        messageId="msg-failed"
        isUser={false}
        onImageClick={vi.fn()}
        isGenerating={false}
        failedMediaType="image"
        numImages={3}
        content={[
          { type: 'image', url: null, failed: true },
          { type: 'image', url: null, failed: true },
          { type: 'image', url: null, failed: true },
        ]}
      />,
    );

    expect(screen.getByTestId('ai-image-grid')).toHaveAttribute('data-num-images', '3');
    expect(screen.queryByTestId('failed-placeholder')).not.toBeInTheDocument();
  });

  it('用户混合附件按 content 顺序分段展示', () => {
    const { container } = render(
      <MessageMedia
        imageAssets={[
          { originalUrl: 'https://cdn/a.png' },
          { originalUrl: 'https://cdn/c.png' },
        ]}
        files={[
          { type: 'file', url: 'https://cdn/b.pdf', name: 'b.pdf', mime_type: 'application/pdf', size: 10 },
          { type: 'file', url: 'https://cdn/d.pdf', name: 'd.pdf', mime_type: 'application/pdf', size: 20 },
        ]}
        content={[
          { type: 'text', text: '按顺序处理' },
          { type: 'image', url: 'https://cdn/a.png' },
          { type: 'file', url: 'https://cdn/b.pdf', name: 'b.pdf', mime_type: 'application/pdf', size: 10 },
          { type: 'image', url: 'https://cdn/c.png' },
          { type: 'file', url: 'https://cdn/d.pdf', name: 'd.pdf', mime_type: 'application/pdf', size: 20 },
        ]}
        messageId="msg-mixed"
        isUser
        onImageClick={vi.fn()}
      />,
    );

    expect([...container.querySelectorAll('[data-attachment-run]')]
      .map((element) => element.getAttribute('data-attachment-run')))
      .toEqual(['visuals', 'files', 'visuals', 'files']);
    expect(screen.getByText('b.pdf')).toBeInTheDocument();
    expect(screen.getByText('d.pdf')).toBeInTheDocument();
  });

  it('用户多图使用统一固定卡片尺寸并横向排版', () => {
    const { container } = render(
      <MessageMedia
        imageAssets={[
          { originalUrl: 'https://cdn/a.png' },
          { originalUrl: 'https://cdn/b.png' },
        ]}
        content={[
          { type: 'image', url: 'https://cdn/a.png' },
          { type: 'image', url: 'https://cdn/b.png' },
        ]}
        messageId="msg-two-images"
        isUser
        onImageClick={vi.fn()}
      />,
    );

    const visualRun = container.querySelector('[data-attachment-run="visuals"]');
    expect(visualRun?.getAttribute('style')).toContain(
      'grid-template-columns: repeat(auto-fit, 180px)',
    );
    expect(container.querySelectorAll('[aria-label^="查看图片"]').length).toBe(2);
    expect([...container.querySelectorAll('[aria-label^="查看图片"]')].map((element) => {
      const style = (element as HTMLElement).style;
      return [style.width, style.height];
    })).toEqual([['180px', '180px'], ['180px', '180px']]);
  });

  it('用户多文件使用受控卡片宽度并横向排列，超出后换行', () => {
    const { container } = render(
      <MessageMedia
        content={[
          { type: 'file', url: 'https://cdn/a.xlsx', name: 'a.xlsx', mime_type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', size: 10 },
          { type: 'file', url: 'https://cdn/b.pdf', name: 'b.pdf', mime_type: 'application/pdf', size: 20 },
          { type: 'file', url: 'https://cdn/c.docx', name: 'c.docx', mime_type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document', size: 30 },
        ]}
        messageId="msg-three-files"
        isUser
        onImageClick={vi.fn()}
      />,
    );

    const fileList = container.querySelector('[data-file-card-list]');
    expect(fileList?.className).toContain('flex-wrap');
    expect(fileList?.querySelectorAll('[data-file-card]')).toHaveLength(3);
    expect([...fileList?.querySelectorAll('[data-file-card]') || []].every((card) => (
      card.className.includes('max-w-[280px]') && card.className.includes('flex-[0_1_280px]')
    ))).toBe(true);
  });
});
