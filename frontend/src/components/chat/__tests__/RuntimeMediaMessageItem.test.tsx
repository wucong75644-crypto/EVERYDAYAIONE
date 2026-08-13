import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import type { Message } from '../../../types/message';
import MessageItem from '../message/MessageItem';

let capturedMediaProps: Record<string, unknown> = {};

vi.mock('../message/MessageMedia', () => ({
  default: (props: Record<string, unknown>) => {
    capturedMediaProps = props;
    return <div data-testid="message-media" />;
  },
}));
vi.mock('../message/MessageActions', () => ({ default: () => null }));
vi.mock('../modals/DeleteMessageModal', () => ({ default: () => null }));
vi.mock('../../../preview/PreviewHost', () => ({ default: () => null }));
vi.mock('../message/MarkdownRenderer', () => ({
  default: ({ content }: { content: string }) => <span>{content}</span>,
}));
vi.mock('../../../utils/settingsStorage', () => ({
  getSavedSettings: () => ({
    image: { aspectRatio: '1:1' },
    video: { aspectRatio: 'landscape' },
  }),
}));
vi.mock('../../../hooks/useModalAnimation', () => ({
  useModalAnimation: () => ({ isOpen: false, open: vi.fn(), close: vi.fn() }),
}));
vi.mock('../../../hooks/useMessageAnimation', () => ({
  useMessageAnimation: () => ({ entryAnimationClass: '', deleteAnimationClass: '' }),
}));
vi.mock('../../../stores/useMessageStore', () => ({
  useMessageStore: { getState: () => ({ updateMessage: vi.fn() }) },
  getTextContent: (message: Message) => message.content
    .filter((part) => part.type === 'text')
    .map((part) => part.type === 'text' ? part.text : '')
    .join('\n\n'),
  getImageAssets: (message: Message) => message.content.flatMap((part) => (
    part.type === 'image' && part.url ? [{ originalUrl: part.url }] : []
  )),
  getVideoUrls: () => [],
  getFiles: () => [],
}));

function makeMessage(overrides: Partial<Message>): Message {
  return {
    id: 'runtime-message',
    conversation_id: 'conversation-1',
    role: 'assistant',
    content: [],
    status: 'completed',
    generation_params: { type: 'chat' },
    created_at: '2026-08-14T00:00:00Z',
    ...overrides,
  };
}

function runtimeSlots(completed: Set<number>): Message['content'] {
  return Array.from({ length: 10 }, (_, index) => ({
    type: 'image' as const,
    url: completed.has(index) ? `https://runtime/${index}.png` : null,
    slot_id: `slot-${index}`,
    slot_index: index,
    slot_status: completed.has(index) ? 'completed' as const : 'pending' as const,
    slot_revision: completed.has(index) ? 1 : 0,
  }));
}

describe('MessageItem Runtime media batch', () => {
  beforeEach(() => {
    capturedMediaProps = {};
  });

  it('renders final text once beside ten completed chat slots', () => {
    const slots = runtimeSlots(new Set(Array.from({ length: 10 }, (_, index) => index)));
    render(
      <MessageItem
        message={makeMessage({
          content: [{ type: 'text', text: '十张图片已经生成完成' }, ...slots],
        })}
        allImageAssets={slots.flatMap((part) => (
          part.type === 'image' && part.url ? [{ originalUrl: part.url }] : []
        ))}
      />,
    );

    expect(screen.getAllByText('十张图片已经生成完成')).toHaveLength(1);
    expect(screen.getByTestId('message-media')).toBeInTheDocument();
    expect(capturedMediaProps.numImages).toBe(10);
    expect(capturedMediaProps.isGenerating).toBe(false);
  });

  it('keeps the batch generating after the first slot completes', () => {
    render(
      <MessageItem
        message={makeMessage({ content: runtimeSlots(new Set([0])), status: 'pending' })}
      />,
    );

    expect(capturedMediaProps.numImages).toBe(10);
    expect(capturedMediaProps.isGenerating).toBe(true);
  });

  it('keeps image_ecom on its legacy media path', () => {
    render(
      <MessageItem
        message={makeMessage({
          generation_params: { type: 'image_ecom', num_images: 2 },
          content: [
            { type: 'image', url: 'https://ecom/0.png' },
            { type: 'image', url: 'https://ecom/1.png' },
          ],
        })}
      />,
    );

    expect(capturedMediaProps.numImages).toBe(2);
    expect(capturedMediaProps.isGenerating).toBe(false);
  });
});
