/**
 * MessageItem 单元测试（回调稳定性）
 *
 * 测试覆盖：
 * 1. handleImageClick useCallback 合并逻辑正确
 * 2. handleRegenerateSingle useCallback 正确绑定 message.id
 * 3. handleRegenerate useCallback 正确绑定 message.id
 * 4. 回调引用在 message 不变时保持稳定
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import MessageItem from '../message/MessageItem';
import type { Message } from '../../../stores/useMessageStore';

// ============================================================
// Mock 配置
// ============================================================

// 捕获 MessageMedia 接收到的 props
let capturedMediaProps: Record<string, unknown> = {};

vi.mock('../message/MessageMedia', () => ({
  default: (props: Record<string, unknown>) => {
    capturedMediaProps = props;
    return <div data-testid="message-media" />;
  },
}));

vi.mock('../message/MessageActions', () => ({
  default: () => <div data-testid="message-actions" />,
}));

vi.mock('../modals/DeleteMessageModal', () => ({
  default: () => null,
}));

vi.mock('../media/ImagePreviewModal', () => ({
  default: () => null,
}));

// 捕获 FailedMediaPlaceholder 渲染（电商图模式 failed ImagePart 测试用）
let capturedFailedPlaceholderProps: Record<string, unknown> | null = null;
vi.mock('../media/MediaPlaceholder', () => ({
  FailedMediaPlaceholder: (props: Record<string, unknown>) => {
    capturedFailedPlaceholderProps = props;
    return <div data-testid="failed-media-placeholder" />;
  },
}));

vi.mock('../../../services/api', () => ({
  default: { post: vi.fn() },
}));

vi.mock('../message/LoadingPlaceholder', () => ({
  default: ({ text }: { text: string }) => <span>{text}</span>,
}));

vi.mock('../../../utils/settingsStorage', () => ({
  getSavedSettings: () => ({
    image: { aspectRatio: '1:1' },
    video: { aspectRatio: 'landscape' },
  }),
}));

vi.mock('../../../hooks/useModalAnimation', () => ({
  useModalAnimation: () => ({
    isOpen: false,
    isClosing: false,
    open: vi.fn(),
    close: vi.fn(),
  }),
}));

vi.mock('../../../hooks/useMessageAnimation', () => ({
  useMessageAnimation: () => ({
    entryAnimationClass: '',
    deleteAnimationClass: '',
  }),
}));

const mockUpdateMessage = vi.fn();
vi.mock('../../../stores/useMessageStore', () => ({
  useMessageStore: { getState: () => ({ updateMessage: mockUpdateMessage }) },
  getTextContent: (msg: Message) => {
    if (!msg.content || msg.content.length === 0) return '';
    const text = msg.content.find((p: { type: string }) => p.type === 'text');
    return text && 'text' in text ? (text as { text: string }).text : '';
  },
  getImageAssets: (msg: Message) =>
    msg.content
      .filter((p: { type: string }) => p.type === 'image' && 'url' in p && (p as { url: string | null }).url)
      .map((p: { type: string; original_url?: string; thumbnail_url?: string; url?: string }) => ({
        originalUrl: p.original_url || p.url!,
        thumbnailUrl: p.thumbnail_url,
      })),
  getVideoUrls: () => [],
  getFiles: () => [],
}));

vi.mock('../../../constants/placeholder', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../constants/placeholder')>();
  return {
    ...actual,
    PLACEHOLDER_TEXT: {
      CHAT_THINKING: '思考中',
      IMAGE_GENERATING: '正在生成图片',
      VIDEO_GENERATING: '正在生成视频',
    },
  };
});

// ============================================================
// 测试
// ============================================================

function makeMessage(overrides: Partial<Message> = {}): Message {
  return {
    id: 'msg-1',
    conversation_id: 'conv-1',
    role: 'assistant',
    content: [
      { type: 'image', url: 'https://img1.png' },
      { type: 'image', url: 'https://img2.png' },
    ],
    status: 'completed',
    generation_params: { type: 'image', num_images: 2 },
    created_at: '2026-01-01',
    ...overrides,
  };
}

describe('MessageItem 回调稳定性', () => {
  beforeEach(() => {
    capturedMediaProps = {};
    capturedFailedPlaceholderProps = null;
    mockUpdateMessage.mockClear();
  });

  it('传递 handleImageClick 给 MessageMedia（非内联箭头）', () => {
    const msg = makeMessage();
    render(
      <MessageItem
        message={msg}
        allImageAssets={[{ originalUrl: 'https://img1.png' }, { originalUrl: 'https://img2.png' }]}
        currentImageIndex={0}
      />,
    );
    expect(screen.getByTestId('message-media')).toBeInTheDocument();
    expect(typeof capturedMediaProps.onImageClick).toBe('function');
  });

  it('handleRegenerateSingle 绑定 message.id 并正确传递 imageIndex', () => {
    const onRegenerateSingle = vi.fn();
    const msg = makeMessage();
    render(
      <MessageItem
        message={msg}
        onRegenerateSingle={onRegenerateSingle}
        allImageAssets={[{ originalUrl: 'https://img1.png' }, { originalUrl: 'https://img2.png' }]}
        currentImageIndex={0}
      />,
    );
    // MessageMedia 收到 onRegenerateSingle
    const handler = capturedMediaProps.onRegenerateSingle as (idx: number) => void;
    expect(typeof handler).toBe('function');

    // 调用时应绑定 message.id
    handler(1);
    expect(onRegenerateSingle).toHaveBeenCalledWith('msg-1', 1);
  });

  it('handleRegenerate 绑定 message.id', () => {
    const onRegenerate = vi.fn();
    const msg = makeMessage({ status: 'failed' });
    render(
      <MessageItem
        message={msg}
        onRegenerate={onRegenerate}
        allImageAssets={[]}
        currentImageIndex={0}
      />,
    );
    const handler = capturedMediaProps.onRegenerate as () => void;
    expect(typeof handler).toBe('function');

    handler();
    expect(onRegenerate).toHaveBeenCalledWith('msg-1');
  });

  it('onRegenerateSingle 未提供时 MessageMedia 收到 undefined', () => {
    const msg = makeMessage();
    render(
      <MessageItem
        message={msg}
        allImageAssets={[{ originalUrl: 'https://img1.png' }]}
        currentImageIndex={0}
      />,
    );
    expect(capturedMediaProps.onRegenerateSingle).toBeUndefined();
  });

  it('回调引用在相同 message 重渲染时保持稳定', () => {
    const onRegenerateSingle = vi.fn();
    const msg = makeMessage();
    const { rerender } = render(
      <MessageItem
        message={msg}
        onRegenerateSingle={onRegenerateSingle}
        allImageAssets={[{ originalUrl: 'https://img1.png' }, { originalUrl: 'https://img2.png' }]}
        currentImageIndex={0}
      />,
    );
    const firstRef = capturedMediaProps.onRegenerateSingle;

    // 相同 message 对象重渲染
    rerender(
      <MessageItem
        message={msg}
        onRegenerateSingle={onRegenerateSingle}
        allImageAssets={[{ originalUrl: 'https://img1.png' }, { originalUrl: 'https://img2.png' }]}
        currentImageIndex={0}
      />,
    );
    const secondRef = capturedMediaProps.onRegenerateSingle;

    // memo 组件 props 不变 → 不会重渲染 → props 引用应一致
    // 但即使重渲染了，useCallback 也保证引用稳定
    expect(firstRef).toBe(secondRef);
  });
});


// ============================================================
// Phase 5: 中断态视觉信号（灰字提示 + interrupt_marker 跳过）
// 详见 docs/document/TECH_用户中断与恢复机制.md §15.5
// ============================================================

function makeInterruptedMessage(overrides: Partial<Message> = {}): Message {
  const fiveMinAgo = new Date(Date.now() - 5 * 60 * 1000).toISOString();
  return {
    id: 'msg-interrupted',
    conversation_id: 'conv-1',
    role: 'assistant',
    content: [
      { type: 'text', text: '我先查最近订单' },
      {
        type: 'tool_step' as const,
        tool_name: 'erp_query',
        tool_call_id: 'call_A',
        status: 'cancelled' as const,
        cancelled_at: fiveMinAgo,
      },
      {
        type: 'interrupt_marker' as const,
        interrupted_at: fiveMinAgo,
        reason: 'user_cancel' as const,
      },
    ] as Message['content'],
    status: 'interrupted',
    created_at: '2026-06-05T10:00:00Z',
    ...overrides,
  };
}

describe('MessageItem 中断态渲染', () => {
  beforeEach(() => {
    capturedMediaProps = {};
    mockUpdateMessage.mockClear();
  });

  it('status=interrupted 且含 interrupt_marker → 显示灰字提示"停止于 X 前"', () => {
    const msg = makeInterruptedMessage();
    render(
      <MessageItem
        message={msg}
        allImageAssets={[]}
        currentImageIndex={0}
      />,
    );
    const hint = screen.getByTestId('interrupt-hint');
    expect(hint).toBeInTheDocument();
    expect(hint.textContent).toContain('停止于');
    expect(hint.textContent).toContain('分钟前');
  });

  it('status=interrupted 但无 interrupt_marker → 不显示提示', () => {
    const msg = makeInterruptedMessage({
      content: [
        { type: 'text', text: '部分内容' },
        {
          type: 'tool_step' as const,
          tool_name: 'erp_query',
          tool_call_id: 'call_A',
          status: 'cancelled' as const,
        },
      ] as Message['content'],
    });
    render(
      <MessageItem
        message={msg}
        allImageAssets={[]}
        currentImageIndex={0}
      />,
    );
    expect(screen.queryByTestId('interrupt-hint')).toBeNull();
  });

  it('status=completed 即使含 interrupt_marker 也不显示提示', () => {
    const msg = makeInterruptedMessage({ status: 'completed' });
    render(
      <MessageItem
        message={msg}
        allImageAssets={[]}
        currentImageIndex={0}
      />,
    );
    expect(screen.queryByTestId('interrupt-hint')).toBeNull();
  });

  it('interrupt_marker block 不渲染为独立卡片', () => {
    const msg = makeInterruptedMessage();
    const { container } = render(
      <MessageItem
        message={msg}
        allImageAssets={[]}
        currentImageIndex={0}
      />,
    );
    // 整个组件渲染后，DOM 内不应出现 "interrupt_marker" 字面量文本
    expect(container.textContent).not.toContain('interrupt_marker');
    expect(container.textContent).not.toContain('user_cancel');
  });
});


// ============================================================
// 电商图模式：failed ImagePart 渲染测试
// ============================================================

describe('MessageItem failed ImagePart', () => {
  beforeEach(() => {
    capturedFailedPlaceholderProps = null;
  });

  it('渲染 FailedMediaPlaceholder 当 content 包含 failed image', () => {
    const failedMessage = makeMessage({
      content: [
        { type: 'text', text: '图片生成失败' },
        {
          type: 'image',
          url: null,
          width: 800,
          height: 800,
          failed: true,
          error: '服务繁忙',
          retry_context: {
            task: '白底主图',
            image_urls: [],
            platform: 'taobao',
            style_directive: '',
          },
        },
      ],
      generation_params: { type: 'chat' },
      status: 'completed',
    });

    render(
      <MessageItem
        message={failedMessage}
        allImageAssets={[]}
        currentImageIndex={0}
      />,
    );

    // 验证 FailedMediaPlaceholder 被渲染
    expect(screen.getByTestId('failed-media-placeholder')).toBeTruthy();
    // 验证传入了正确的 props
    expect(capturedFailedPlaceholderProps).not.toBeNull();
    expect(capturedFailedPlaceholderProps?.type).toBe('image');
    expect(capturedFailedPlaceholderProps?.retryLabel).toBe('重新生成');
    // 验证 onRetry 回调存在（有 retry_context 时）
    expect(typeof capturedFailedPlaceholderProps?.onRetry).toBe('function');
  });

  it('正常图片不渲染 FailedMediaPlaceholder', () => {
    const normalMessage = makeMessage({
      content: [
        { type: 'text', text: '图片已生成' },
        { type: 'image', url: 'https://cdn/img.png', width: 800, height: 800 },
      ],
      generation_params: { type: 'chat' },
      status: 'completed',
    });

    render(
      <MessageItem
        message={normalMessage}
        allImageAssets={[{ originalUrl: 'https://cdn/img.png' }]}
        currentImageIndex={0}
      />,
    );

    // 正常图片不应渲染 FailedMediaPlaceholder
    expect(screen.queryByTestId('failed-media-placeholder')).toBeNull();
  });

  it('L3 失败图(无 retry_context)走 onRegenerateSingle fallback', () => {
    // L3 异步任务失败的 ImagePart 不带 retry_context,
    // 但有 onRegenerateSingle prop 时应该提供 onRetry(走 fallback 路径)
    const onRegenerateSingle = vi.fn();
    const l3FailedMessage = makeMessage({
      content: [
        { type: 'text', text: '图片生成失败' },
        {
          type: 'image',
          url: null,
          width: 800,
          height: 800,
          failed: true,
          error: '生成失败',
          // 关键:无 retry_context(L3 不填这个字段)
        },
      ],
      generation_params: { type: 'image' },
      status: 'completed',
    });

    render(
      <MessageItem
        message={l3FailedMessage}
        allImageAssets={[]}
        currentImageIndex={0}
        onRegenerateSingle={onRegenerateSingle}
      />,
    );

    // FailedMediaPlaceholder 仍然渲染
    expect(screen.getByTestId('failed-media-placeholder')).toBeTruthy();
    // onRetry 应该存在(走 onRegenerateSingle fallback)
    expect(typeof capturedFailedPlaceholderProps?.onRetry).toBe('function');
    // 触发 onRetry 应该调到 onRegenerateSingle
    capturedFailedPlaceholderProps?.onRetry?.();
    expect(onRegenerateSingle).toHaveBeenCalledTimes(1);
  });

  it('L3 失败图无 onRegenerateSingle prop 时不显示重试按钮', () => {
    const l3FailedMessage = makeMessage({
      content: [
        {
          type: 'image',
          url: null,
          failed: true,
          error: '生成失败',
        },
      ],
      generation_params: { type: 'image' },
      status: 'completed',
    });

    render(
      <MessageItem
        message={l3FailedMessage}
        allImageAssets={[]}
        currentImageIndex={0}
        // 不传 onRegenerateSingle
      />,
    );

    // 占位符渲染,但 onRetry 应为 undefined(无按钮)
    expect(screen.getByTestId('failed-media-placeholder')).toBeTruthy();
    expect(capturedFailedPlaceholderProps?.onRetry).toBeUndefined();
  });
});
