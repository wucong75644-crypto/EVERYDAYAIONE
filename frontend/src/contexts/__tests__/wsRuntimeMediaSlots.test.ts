import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { Message } from '../../types/message';
import { handleImagePartialUpdate } from '../wsTaskMessageHandlers';
import type { HandlerDeps, WSIncomingMessage } from '../wsMessageHandlerShared';

vi.mock('../../utils/logger', () => ({
  logger: { info: vi.fn(), warn: vi.fn(), error: vi.fn() },
}));

function runtimeSlot(index: number) {
  return {
    type: 'image' as const,
    url: null,
    slot_id: `slot-${index}`,
    slot_index: index,
    slot_status: 'pending' as const,
    slot_revision: 0,
  };
}

function message(content: Message['content']): Message {
  return {
    id: 'runtime-message',
    conversation_id: 'conversation-1',
    role: 'assistant',
    content,
    status: 'pending',
    generation_params: { type: 'chat' },
    created_at: '2026-08-14T00:00:00Z',
  };
}

function partialUpdate(payload: Record<string, unknown>): WSIncomingMessage {
  return {
    type: 'image_partial_update',
    timestamp: Date.now(),
    message_id: 'runtime-message',
    payload,
  };
}

describe('Runtime image_partial_update', () => {
  let current: Message;
  let updateMessage: ReturnType<typeof vi.fn>;
  let deps: HandlerDeps;

  beforeEach(() => {
    current = message([]);
    updateMessage = vi.fn((_messageId: string, update: Partial<Message>) => {
      current = { ...current, ...update };
    });
    deps = {
      getStore: () => ({
        getMessage: () => current,
        updateMessage,
      }),
    } as unknown as HandlerDeps;
  });

  it('updates slot 9 inside ten mixed-content slots without moving text', () => {
    current = message([
      { type: 'text', text: '分析说明' },
      ...Array.from({ length: 10 }, (_, offset) => runtimeSlot(9 - offset)),
      { type: 'text', text: '最终说明' },
    ]);

    handleImagePartialUpdate(deps, partialUpdate({
      slot_id: 'slot-9',
      slot_index: 9,
      slot_status: 'completed',
      slot_revision: 1,
      content_part: { type: 'image', url: 'https://oss/runtime-9.png' },
      completed_count: 1,
      total_count: 10,
    }));

    expect(current.content[0]).toEqual({ type: 'text', text: '分析说明' });
    expect(current.content.at(-1)).toEqual({ type: 'text', text: '最终说明' });
    expect(current.content.filter((part) => part.type === 'image')).toHaveLength(10);
    expect(current.content.find((part) => part.type === 'image' && part.slot_id === 'slot-9'))
      .toMatchObject({
        url: 'https://oss/runtime-9.png', slot_index: 9,
        slot_status: 'completed', slot_revision: 1,
      });
  });

  it('ignores duplicate and older revisions after a terminal update', () => {
    current = message([{
      ...runtimeSlot(0),
      url: 'https://oss/final.png',
      slot_status: 'completed',
      slot_revision: 4,
    }]);

    for (const slotRevision of [3, 4]) {
      handleImagePartialUpdate(deps, partialUpdate({
        slot_id: 'slot-0', slot_index: 0,
        slot_status: 'failed', slot_revision: slotRevision,
        error: 'late failure',
      }));
    }

    expect(updateMessage).not.toHaveBeenCalled();
    expect(current.content[0]).toMatchObject({
      url: 'https://oss/final.png', slot_status: 'completed', slot_revision: 4,
    });
  });

  it('records accepted to unknown without replacing surrounding content', () => {
    current = message([
      { type: 'text', text: '说明' },
      { ...runtimeSlot(2), slot_status: 'accepted', slot_revision: 1 },
    ]);

    handleImagePartialUpdate(deps, partialUpdate({
      slot_id: 'slot-2', slot_index: 2,
      slot_status: 'unknown', slot_revision: 2,
    }));

    expect(current.content).toEqual([
      { type: 'text', text: '说明' },
      expect.objectContaining({
        slot_id: 'slot-2', slot_status: 'unknown', slot_revision: 2,
      }),
    ]);
  });

  it('clears an old failure when a newer retry returns to pending', () => {
    current = message([{
      ...runtimeSlot(1),
      slot_status: 'failed',
      slot_revision: 2,
      failed: true,
      error: 'old failure',
    }]);

    handleImagePartialUpdate(deps, partialUpdate({
      slot_id: 'slot-1', slot_index: 1,
      slot_status: 'pending', slot_revision: 3,
    }));

    expect(current.content[0]).toMatchObject({
      slot_status: 'pending', slot_revision: 3, failed: false,
    });
    expect(current.content[0]).not.toHaveProperty('error');
  });

  it('rejects conflicting payload and content-part slot identities', () => {
    current = message([runtimeSlot(0), runtimeSlot(1)]);

    handleImagePartialUpdate(deps, partialUpdate({
      slot_id: 'slot-0', slot_index: 0,
      slot_status: 'completed', slot_revision: 1,
      content_part: {
        type: 'image', url: 'https://oss/wrong.png',
        slot_id: 'slot-1', slot_index: 1,
        slot_status: 'completed', slot_revision: 1,
      },
    }));

    expect(updateMessage).not.toHaveBeenCalled();
  });

  it('keeps a completed image when a later batch cancel arrives', () => {
    current = message([{
      ...runtimeSlot(0),
      url: 'https://oss/completed.png',
      slot_status: 'completed',
      slot_revision: 4,
    }]);

    handleImagePartialUpdate(deps, partialUpdate({
      slot_id: 'slot-0', slot_index: 0,
      slot_status: 'cancelled', slot_revision: 5,
    }));

    expect(updateMessage).not.toHaveBeenCalled();
    expect(current.content[0]).toMatchObject({
      url: 'https://oss/completed.png', slot_status: 'completed',
    });
  });

  it('uses legacy image_index within image parts and preserves ecom retry data', () => {
    current = message([
      { type: 'text', text: 'before' },
      { type: 'image', url: null },
      { type: 'text', text: 'after' },
      { type: 'image', url: null },
    ]);
    const retryContext = { task: '主图', platform: 'taobao' };

    handleImagePartialUpdate(deps, partialUpdate({
      image_index: 1,
      content_part: {
        type: 'image', url: 'https://oss/ecom-1.png', retry_context: retryContext,
      },
    }));

    expect(current.content[0]).toEqual({ type: 'text', text: 'before' });
    expect(current.content[2]).toEqual({ type: 'text', text: 'after' });
    expect(current.content[3]).toMatchObject({
      url: 'https://oss/ecom-1.png', retry_context: retryContext,
    });
  });
});
