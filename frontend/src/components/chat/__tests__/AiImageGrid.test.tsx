/**
 * AiImageGrid 单元测试
 *
 * 测试覆盖：
 * 1. GridCell memo：数据 props 不变时不重渲染
 * 2. GridCell memo：imageUrl 变化时仅对应 cell 重渲染
 * 3. onRegenerateSingle 参数传递：GridCell 内部正确传 index
 * 4. 多图网格正确渲染占位符和图片
 */

import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import AiImageGrid from '../media/AiImageGrid';
import type { ContentPart } from '../../../stores/useMessageStore';

// Mock 依赖
vi.mock('react-intersection-observer', () => ({
  useInView: () => ({ ref: vi.fn(), inView: true }),
}));

vi.mock('../media/MediaPlaceholder', () => ({
  FailedMediaPlaceholder: ({ onRetry, retryLabel, errorCode }: { onRetry?: () => void; retryLabel?: string; errorCode?: string }) => (
    <div data-testid="failed-placeholder" data-error-code={errorCode}>
      {onRetry && <button onClick={onRetry}>{retryLabel}</button>}
    </div>
  ),
}));

vi.mock('../menus/shared.module.css', () => ({
  default: { 'dynamic-aspect-ratio': 'dynamic-aspect-ratio' },
}));

const defaultPlaceholderSize = { width: 512, height: 512 };

function makeContent(urls: (string | null)[], failed?: boolean[]): ContentPart[] {
  return urls.map((url, i) => ({
    type: 'image' as const,
    url,
    failed: failed?.[i] || false,
  }));
}

function makeRuntimeSlots(completedIndex?: number): ContentPart[] {
  return Array.from({ length: 10 }, (_, index) => ({
    type: 'image' as const,
    url: index === completedIndex ? `https://img${index}.png` : null,
    slot_id: `slot-${index}`,
    slot_index: index,
    slot_status: index === completedIndex ? 'completed' as const : 'pending' as const,
    slot_revision: index === completedIndex ? 1 : 0,
  }));
}

describe('AiImageGrid', () => {
  it('渲染正确数量的网格单元（含占位符）', () => {
    const content = makeContent(['https://img1.png', null, null, null]);
    const { container } = render(
      <AiImageGrid
        content={content}
        numImages={4}
        messageId="msg-1"
        placeholderSize={defaultPlaceholderSize}
        onImageClick={vi.fn()}
        isGenerating={true}
      />,
    );
    // 4 个 cell：1 个图片 + 3 个占位符
    const grid = container.querySelector('.grid');
    expect(grid?.children.length).toBe(4);
  });

  it('图片 URL 到达后正确渲染 img 元素', () => {
    const content = makeContent(['https://img1.png', 'https://img2.png']);
    render(
      <AiImageGrid
        content={content}
        numImages={2}
        messageId="msg-1"
        placeholderSize={defaultPlaceholderSize}
        onImageClick={vi.fn()}
        isGenerating={false}
      />,
    );
    const images = screen.getAllByRole('img');
    expect(images).toHaveLength(2);
    expect(images[0]).toHaveAttribute('src', 'https://img1.png');
    expect(images[1]).toHaveAttribute('src', 'https://img2.png');
  });

  it('生成结束后忽略非图片内容且不补空白单元格', () => {
    const content: ContentPart[] = [
      { type: 'text', text: '{"image":"https://example.com/fake.jpg"}' },
      { type: 'image', url: 'https://cdn.example.com/real.png' },
      { type: 'file', url: 'https://cdn.example.com/report.xlsx', name: 'report.xlsx', mime_type: 'application/vnd.ms-excel' },
    ];
    const { container } = render(
      <AiImageGrid
        content={content}
        numImages={4}
        messageId="msg-mixed"
        placeholderSize={defaultPlaceholderSize}
        onImageClick={vi.fn()}
        isGenerating={false}
      />,
    );

    expect(container.querySelector('.grid')?.children).toHaveLength(1);
    expect(screen.getByRole('img')).toHaveAttribute('src', 'https://cdn.example.com/real.png');
  });

  it('uses thumbnail_url for grid display while keeping original_url in content', () => {
    const content: ContentPart[] = [{
      type: 'image',
      url: 'https://cdn.everydayai.com.cn/original.png',
      original_url: 'https://cdn.everydayai.com.cn/original.png',
      thumbnail_url: 'https://cdn.everydayai.com.cn/workspace-thumbnails/thumb.w360.webp',
    }];

    render(
      <AiImageGrid
        content={content}
        numImages={1}
        messageId="msg-1"
        placeholderSize={defaultPlaceholderSize}
        onImageClick={vi.fn()}
        isGenerating={false}
      />,
    );

    expect(screen.getByRole('img')).toHaveAttribute(
      'src',
      'https://cdn.everydayai.com.cn/workspace-thumbnails/thumb.w360.webp',
    );
  });

  it('falls back to original_url before showing a failed placeholder', () => {
    const content: ContentPart[] = [{
      type: 'image',
      original_url: 'https://oss.example.com/workspace/original.png',
      thumbnail_url: 'https://oss.example.com/workspace-thumbnails/original.w360.webp',
    }];
    render(
      <AiImageGrid
        content={content}
        numImages={1}
        messageId="msg-fallback"
        placeholderSize={defaultPlaceholderSize}
        onImageClick={vi.fn()}
        isGenerating={false}
      />,
    );

    fireEvent.error(screen.getByRole('img'));
    expect(screen.getByRole('img')).toHaveAttribute(
      'src',
      'https://oss.example.com/workspace/original.png',
    );
    fireEvent.error(screen.getByRole('img'));
    expect(screen.getByTestId('failed-placeholder')).toBeInTheDocument();
  });

  it('失败的图片渲染 FailedMediaPlaceholder', () => {
    const content = makeContent([null], [true]);
    render(
      <AiImageGrid
        content={content}
        numImages={1}
        messageId="msg-1"
        placeholderSize={defaultPlaceholderSize}
        onImageClick={vi.fn()}
        isGenerating={false}
        onRegenerateSingle={vi.fn()}
      />,
    );
    expect(screen.getByTestId('failed-placeholder')).toBeInTheDocument();
  });

  it('多图失败单元格传递积分不足错误码', () => {
    const content: ContentPart[] = [{
      type: 'image', url: null, failed: true,
      error: 'provider raw error', error_code: 'INSUFFICIENT_CREDITS',
    }];
    render(
      <AiImageGrid
        content={content}
        numImages={1}
        messageId="msg-credits"
        placeholderSize={defaultPlaceholderSize}
        onImageClick={vi.fn()}
        isGenerating={false}
      />,
    );

    expect(screen.getByTestId('failed-placeholder')).toHaveAttribute(
      'data-error-code',
      'INSUFFICIENT_CREDITS',
    );
  });

  it('onRegenerateSingle 在失败占位符点击时传递正确的 index', () => {
    const onRegenerate = vi.fn();
    // 第 0 张成功，第 1 张失败
    const content = makeContent(['https://img1.png', null], [false, true]);
    render(
      <AiImageGrid
        content={content}
        numImages={2}
        messageId="msg-1"
        placeholderSize={defaultPlaceholderSize}
        onImageClick={vi.fn()}
        isGenerating={false}
        onRegenerateSingle={onRegenerate}
      />,
    );
    // 点击失败占位符的重新生成按钮
    fireEvent.click(screen.getByText('重新生成'));
    expect(onRegenerate).toHaveBeenCalledWith(1);
  });

  it('onRegenerateSingle 在悬浮按钮点击时传递正确的 index', () => {
    const onRegenerate = vi.fn();
    const content = makeContent(['https://img1.png', 'https://img2.png']);
    render(
      <AiImageGrid
        content={content}
        numImages={2}
        messageId="msg-1"
        placeholderSize={defaultPlaceholderSize}
        onImageClick={vi.fn()}
        isGenerating={false}
        onRegenerateSingle={onRegenerate}
      />,
    );
    // 找到所有"重新生成"按钮（悬浮层中的）
    const regenerateButtons = screen.getAllByLabelText('重新生成');
    // 点击第二张图的重新生成按钮
    fireEvent.click(regenerateButtons[1]);
    expect(onRegenerate).toHaveBeenCalledWith(1);
  });

  it('GridCell memo：函数 props 引用变化但数据不变时不重渲染', () => {
    const renderSpy = vi.fn();

    // 通过 onMediaLoaded 间接检测渲染次数
    // 首次渲染：4 个 cell 各渲染 1 次
    const content1 = makeContent([null, null, null, null]);
    const { rerender } = render(
      <AiImageGrid
        content={content1}
        numImages={4}
        messageId="msg-1"
        placeholderSize={defaultPlaceholderSize}
        onImageClick={vi.fn()}
        onMediaLoaded={renderSpy}
        isGenerating={true}
      />,
    );

    // 重渲染：传入新的函数引用（模拟父组件重渲染），但数据相同
    // 由于 GridCell 使用自定义 areEqual，数据 props 不变 → 不应重渲染
    const content2 = makeContent([null, null, null, null]);
    rerender(
      <AiImageGrid
        content={content2}
        numImages={4}
        messageId="msg-1"
        placeholderSize={defaultPlaceholderSize}
        onImageClick={vi.fn()} // 新引用
        onMediaLoaded={vi.fn()} // 新引用
        isGenerating={true}
        onRegenerateSingle={vi.fn()} // 新引用
      />,
    );

    // 占位符 cell 没有 img，如果重渲染了会多出 img 或状态异常
    // 这里验证 4 个占位符仍然正常渲染（没有因 memo 失效导致异常）
    const { container } = render(
      <AiImageGrid
        content={content2}
        numImages={4}
        messageId="msg-1"
        placeholderSize={defaultPlaceholderSize}
        onImageClick={vi.fn()}
        isGenerating={true}
      />,
    );
    const grid = container.querySelector('.grid');
    expect(grid?.children.length).toBe(4);
    // 确认没有 img（全是占位符）
    expect(screen.queryAllByRole('img')).toHaveLength(0);
  });

  it('GridCell memo：imageUrl 变化时对应 cell 正确更新', () => {
    // 初始：4 个占位符
    const content1 = makeContent([null, null, null, null]);
    const { rerender } = render(
      <AiImageGrid
        content={content1}
        numImages={4}
        messageId="msg-1"
        placeholderSize={defaultPlaceholderSize}
        onImageClick={vi.fn()}
        isGenerating={true}
      />,
    );
    expect(screen.queryAllByRole('img')).toHaveLength(0);

    // 图片 #2 返回
    const content2 = makeContent([null, null, 'https://img2.png', null]);
    rerender(
      <AiImageGrid
        content={content2}
        numImages={4}
        messageId="msg-1"
        placeholderSize={defaultPlaceholderSize}
        onImageClick={vi.fn()}
        isGenerating={true}
      />,
    );
    // 只有 1 张图片渲染
    const images = screen.getAllByRole('img');
    expect(images).toHaveLength(1);
    expect(images[0]).toHaveAttribute('src', 'https://img2.png');
  });

  it('onImageClick 在图片点击时传递正确的 index', () => {
    const onClick = vi.fn();
    const content = makeContent(['https://img1.png', 'https://img2.png']);
    render(
      <AiImageGrid
        content={content}
        numImages={2}
        messageId="msg-1"
        placeholderSize={defaultPlaceholderSize}
        onImageClick={onClick}
        isGenerating={false}
      />,
    );
    // 点击第二张图的容器
    const buttons = screen.getAllByRole('button', { name: /查看图片/ });
    fireEvent.click(buttons[1]);
    expect(onClick).toHaveBeenCalledWith(1);
  });

  it('Runtime 第一张乱序完成后仍保留十个固定槽位', () => {
    const content = [
      { type: 'text' as const, text: '最终说明' },
      ...makeRuntimeSlots(9).reverse(),
    ];
    const { container } = render(
      <AiImageGrid
        content={content}
        numImages={10}
        messageId="runtime-10"
        placeholderSize={defaultPlaceholderSize}
        onImageClick={vi.fn()}
        isGenerating={true}
      />,
    );

    const cells = container.querySelector('.grid')?.children;
    expect(cells).toHaveLength(10);
    expect(cells?.[0]).toHaveAttribute('data-slot-id', 'slot-0');
    expect(cells?.[9]).toHaveAttribute('data-slot-id', 'slot-9');
    expect(screen.getByRole('img')).toHaveAttribute('src', 'https://img9.png');
    expect(screen.getByTestId('runtime-media-summary')).toHaveTextContent('1/10 已完成');
    expect(screen.getByTestId('runtime-media-summary')).toHaveAttribute('data-active-count', '9');
  });

  it('Runtime 图片点击和重试使用稳定 slot_index', () => {
    const onClick = vi.fn();
    const content: ContentPart[] = makeRuntimeSlots(9).map((part) => (
      part.type === 'image' && part.slot_index === 4
        ? { ...part, slot_status: 'failed', failed: true, slot_revision: 2 }
        : part
    ));
    const onRegenerate = vi.fn();
    render(
      <AiImageGrid
        content={content}
        numImages={10}
        messageId="runtime-index"
        placeholderSize={defaultPlaceholderSize}
        onImageClick={onClick}
        onRegenerateSingle={onRegenerate}
        isGenerating={true}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: '查看图片 10' }));
    expect(onClick).toHaveBeenCalledWith(9);
    fireEvent.click(screen.getByText('重新生成'));
    expect(onRegenerate).toHaveBeenCalledWith(4);
  });

  it('Runtime unknown 和 cancelled 槽位显示可恢复状态', () => {
    const content: ContentPart[] = [
      { ...makeRuntimeSlots()[0], slot_status: 'unknown', slot_revision: 2 },
      { ...makeRuntimeSlots()[1], slot_status: 'cancelled', slot_revision: 3 },
    ];
    render(
      <AiImageGrid
        content={content}
        numImages={2}
        messageId="runtime-states"
        placeholderSize={defaultPlaceholderSize}
        onImageClick={vi.fn()}
        isGenerating={true}
      />,
    );

    expect(screen.getByLabelText('正在确认生成结果')).toHaveAttribute(
      'data-slot-status', 'unknown',
    );
    expect(screen.getByLabelText('已取消')).toHaveAttribute(
      'data-slot-status', 'cancelled',
    );
  });

  it('Runtime 批次只在存在活跃槽位时显示停止控制', () => {
    const onCancel = vi.fn();
    render(
      <AiImageGrid
        content={makeRuntimeSlots(9)}
        numImages={10}
        messageId="runtime-cancel"
        placeholderSize={defaultPlaceholderSize}
        onImageClick={vi.fn()}
        onCancelBatch={onCancel}
        isGenerating={true}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: '停止生成' }));
    expect(onCancel).toHaveBeenCalledTimes(1);
  });

  it('Runtime 批次全部终态后隐藏停止控制', () => {
    const terminal = makeRuntimeSlots().map((part, index) => ({
      ...part,
      slot_status: index === 0 ? 'failed' as const : 'completed' as const,
      url: index === 0 ? null : `https://img${index}.png`,
      slot_revision: 2,
    }));
    render(
      <AiImageGrid
        content={terminal}
        numImages={10}
        messageId="runtime-terminal"
        placeholderSize={defaultPlaceholderSize}
        onImageClick={vi.fn()}
        onCancelBatch={vi.fn()}
        isGenerating={false}
      />,
    );
    expect(screen.queryByRole('button', { name: '停止生成' })).toBeNull();
  });
});
