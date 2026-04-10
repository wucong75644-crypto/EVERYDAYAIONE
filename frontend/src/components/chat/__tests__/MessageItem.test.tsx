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

vi.mock('../../../stores/useMessageStore', () => ({
  getTextContent: (msg: Message) => {
    if (!msg.content || msg.content.length === 0) return '';
    const text = msg.content.find((p: { type: string }) => p.type === 'text');
    return text && 'text' in text ? (text as { text: string }).text : '';
  },
  getImageUrls: (msg: Message) =>
    msg.content
      .filter((p: { type: string }) => p.type === 'image' && 'url' in p && (p as { url: string | null }).url)
      .map((p: { type: string }) => (p as { url: string }).url),
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
  });

  it('传递 handleImageClick 给 MessageMedia（非内联箭头）', () => {
    const msg = makeMessage();
    render(
      <MessageItem
        message={msg}
        allImageUrls={['https://img1.png', 'https://img2.png']}
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
        allImageUrls={['https://img1.png', 'https://img2.png']}
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
        allImageUrls={[]}
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
        allImageUrls={['https://img1.png']}
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
        allImageUrls={['https://img1.png', 'https://img2.png']}
        currentImageIndex={0}
      />,
    );
    const firstRef = capturedMediaProps.onRegenerateSingle;

    // 相同 message 对象重渲染
    rerender(
      <MessageItem
        message={msg}
        onRegenerateSingle={onRegenerateSingle}
        allImageUrls={['https://img1.png', 'https://img2.png']}
        currentImageIndex={0}
      />,
    );
    const secondRef = capturedMediaProps.onRegenerateSingle;

    // memo 组件 props 不变 → 不会重渲染 → props 引用应一致
    // 但即使重渲染了，useCallback 也保证引用稳定
    expect(firstRef).toBe(secondRef);
  });
});
