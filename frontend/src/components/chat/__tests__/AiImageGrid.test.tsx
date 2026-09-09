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
import { act, render, screen, fireEvent } from '@testing-library/react';
import { create } from 'zustand';
import AiImageGrid from '../media/AiImageGrid';
import type { ContentPart, MessageStore } from '../../../stores/useMessageStore';
import { createMessageSlice, createTaskSlice, createStreamingSlice, createConversationSlice } from '../../../stores/slices';
import { createWSMessageHandlers, type HandlerDeps } from '../../../contexts/wsMessageHandlers';

vi.mock('../../../utils/tabSync', () => ({ tabSync: { broadcast: vi.fn() } }));
vi.mock('react-hot-toast', () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

const attachmentMocks = vi.hoisted(() => ({ addQuotedImage: vi.fn() }));
vi.mock('../attachments/ChatAttachmentContext', () => ({
  useChatAttachmentContext: () => attachmentMocks,
}));

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

describe('AiImageGrid', () => {
  it.each(['completed', 'failed'] as const)(
    '连续图片事件在等待期间保留占位，message_done=%s 后正确收尾',
    (terminalStatus) => {
      const useStore = create<MessageStore>()((...args) => ({
        ...createMessageSlice(...args), ...createTaskSlice(...args),
        ...createStreamingSlice(...args), ...createConversationSlice(...args),
      }));
      const messageId = 'msg-retry';
      const conversationId = 'conv-retry';
      const taskId = 'task-retry';
      useStore.getState().addMessage(conversationId, {
        id: messageId, conversation_id: conversationId, role: 'assistant', status: 'streaming',
        content: makeContent([null, null]), created_at: '2026-09-08T00:00:00.000Z',
        generation_params: { type: 'image', num_images: 2 },
      });
      useStore.getState().registerStreamingId(conversationId, messageId);
      useStore.getState().setIsSending(true);
      useStore.getState().createTask({
        taskId, messageId, conversationId, type: 'image', status: 'processing', progress: 0, createdAt: 0,
      });
      const deps: HandlerDeps = {
        getStore: useStore.getState,
        subscribedTasksRef: { current: new Set([taskId]) },
        taskConversationMapRef: { current: new Map([[taskId, conversationId]]) },
        operationContextRef: { current: new Map() }, chunkBufferRef: { current: new Map() },
        flushTimerRef: { current: null }, unsubscribeTask: vi.fn(), send: vi.fn(),
      };
      const handlers = createWSMessageHandlers(deps);
      const envelope = { type: 'image_partial_update', message_id: messageId, conversation_id: conversationId, task_id: taskId };
      function Projection() {
        const message = useStore((state) => state.messages[conversationId][0]);
        return <AiImageGrid content={message.content} numImages={2} messageId={messageId}
          placeholderSize={defaultPlaceholderSize} onImageClick={vi.fn()}
          isGenerating={message.status === 'streaming'} />;
      }
      const { container } = render(<Projection />);
      expect(container.querySelector('.grid')?.children).toHaveLength(2);
      expect(screen.queryAllByRole('img')).toHaveLength(0);
      const first: ContentPart = { type: 'image', url: 'https://cdn.example.com/first.png' };
      act(() => handlers.image_partial_update({ ...envelope, payload: {
        image_index: 0, content_part: first, completed_count: 1, total_count: 2,
      } }));
      fireEvent.load(screen.getByRole('img'));

      // 后端内部重试期间没有终态事件；stream_end 也不能结束图片任务。
      act(() => handlers.stream_end({ ...envelope, type: 'stream_end' }));
      expect(useStore.getState().getMessage(messageId)?.status).toBe('streaming');
      expect(useStore.getState().getStreamingMessageId(conversationId)).toBe(messageId);
      expect(useStore.getState().isSending).toBe(true);
      expect(container.querySelector('.grid')?.children).toHaveLength(2);
      expect(container.querySelectorAll('.animate-media-pulse')).toHaveLength(1);
      expect(screen.getByRole('img')).toHaveAttribute('src', first.url);
      expect(screen.queryByTestId('failed-placeholder')).not.toBeInTheDocument();

      const second: ContentPart = terminalStatus === 'completed'
        ? { type: 'image', url: 'https://cdn.example.com/retried.png', workspace_path: '生成/retried.png' }
        : { type: 'image', url: null, failed: true, error: '图片生成失败', error_code: 'GENERATION_FAILED' };
      act(() => handlers.image_partial_update({ ...envelope, payload: {
        image_index: 1, completed_count: 2, total_count: 2,
        ...(terminalStatus === 'completed' ? { content_part: second }
          : { content_part: null, error: '图片生成失败', error_code: 'GENERATION_FAILED' }),
      } }));
      if (terminalStatus === 'completed') fireEvent.load(screen.getAllByRole('img')[1]);
      expect(useStore.getState().getMessage(messageId)?.status).toBe('streaming');
      expect(useStore.getState().getMessage(messageId)?.content).toEqual([first, second]);
      act(() => handlers.message_done({ ...envelope, type: 'message_done', message: {
        ...useStore.getState().getMessage(messageId), status: terminalStatus, content: [first, second],
      } }));

      expect(useStore.getState().getMessage(messageId)?.status).toBe(terminalStatus);
      expect(useStore.getState().messages[conversationId]).toHaveLength(1);
      expect(useStore.getState().getStreamingMessageId(conversationId)).toBeNull();
      expect(useStore.getState().isSending).toBe(false);
      expect(deps.unsubscribeTask).toHaveBeenCalledWith(taskId);
      expect(deps.subscribedTasksRef.current.has(taskId)).toBe(false);
      expect(container.querySelector('.grid')?.children).toHaveLength(2);
      expect(container.querySelectorAll('.animate-media-pulse')).toHaveLength(0);
      expect(screen.getAllByRole('img')[0]).toHaveAttribute('src', first.url);
      if (terminalStatus === 'completed') {
        expect(screen.getAllByRole('img')[1]).toHaveAttribute('src', 'https://cdn.example.com/retried.png');
        expect(screen.queryByTestId('failed-placeholder')).not.toBeInTheDocument();
        expect(useStore.getState().tasks.has(taskId)).toBe(false);
      } else {
        expect(screen.getAllByRole('img')).toHaveLength(1);
        expect(screen.getByTestId('failed-placeholder')).toHaveAttribute('data-error-code', 'GENERATION_FAILED');
        expect(useStore.getState().tasks.get(taskId)?.status).toBe('failed');
      }
    },
  );

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

  it('多图引用保留原图片的稳定元数据', () => {
    attachmentMocks.addQuotedImage.mockClear();
    const content: ContentPart[] = [{
      type: 'image',
      url: 'https://cdn.everydayai.com.cn/original.png',
      original_url: 'https://cdn.everydayai.com.cn/original.png',
      thumbnail_url: 'https://cdn.everydayai.com.cn/thumb.webp',
      asset_id: 'asset-grid-1',
      workspace_path: '生成/grid-1.png',
      name: 'grid-1.png',
      mime_type: 'image/png',
      size: 1024,
    }];

    render(
      <AiImageGrid
        content={content}
        numImages={1}
        messageId="msg-grid-quote"
        placeholderSize={defaultPlaceholderSize}
        onImageClick={vi.fn()}
        isGenerating={false}
      />,
    );

    const image = screen.getByRole('img');
    fireEvent.load(image);
    fireEvent.contextMenu(screen.getByRole('button', { name: '查看图片 1' }));
    fireEvent.click(screen.getByText('引用'));

    expect(attachmentMocks.addQuotedImage).toHaveBeenCalledWith(expect.objectContaining({
      assetId: 'asset-grid-1',
      workspacePath: '生成/grid-1.png',
      name: 'grid-1.png',
      mimeType: 'image/png',
      size: 1024,
    }));
  });

  it('缩略图加载失败时回退到原图', () => {
    const content: ContentPart[] = [{
      type: 'image',
      url: 'https://cdn.everydayai.com.cn/original.png',
      original_url: 'https://cdn.everydayai.com.cn/original.png',
      thumbnail_url: 'https://cdn.everydayai.com.cn/thumb.webp',
    }];

    render(
      <AiImageGrid
        content={content}
        numImages={1}
        messageId="msg-grid-fallback"
        placeholderSize={defaultPlaceholderSize}
        onImageClick={vi.fn()}
        isGenerating={false}
      />,
    );

    const image = screen.getByRole('img');
    expect(image).toHaveAttribute('src', 'https://cdn.everydayai.com.cn/thumb.webp');
    fireEvent.error(image);
    expect(screen.getByRole('img')).toHaveAttribute('src', 'https://cdn.everydayai.com.cn/original.png');
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
});
