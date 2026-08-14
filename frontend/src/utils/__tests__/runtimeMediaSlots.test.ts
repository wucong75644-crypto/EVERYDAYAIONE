import { describe, expect, it } from 'vitest';
import type { ContentPart } from '../../types/message';
import {
  findImagePartContentIndex,
  getRuntimeMediaImageSlots,
  summarizeRuntimeMediaSlots,
} from '../runtimeMediaSlots';

function slot(index: number, revision = 0): ContentPart {
  return {
    type: 'image',
    url: null,
    slot_id: `slot-${index}`,
    slot_index: index,
    slot_status: index === 9 ? 'unknown' : 'pending',
    slot_revision: revision,
  };
}

describe('runtimeMediaSlots', () => {
  it('sorts ten mixed-content slots by slot_index', () => {
    const content: ContentPart[] = [
      { type: 'text', text: 'final answer' },
      ...Array.from({ length: 10 }, (_, offset) => slot(9 - offset)),
    ];

    expect(getRuntimeMediaImageSlots(content).map((part) => part.slot_index))
      .toEqual([0, 1, 2, 3, 4, 5, 6, 7, 8, 9]);
  });

  it('keeps the newest duplicate slot revision', () => {
    const older = slot(3, 1);
    const newer = { ...slot(3, 2), slot_status: 'completed' as const, url: '/new.png' };

    expect(getRuntimeMediaImageSlots([newer, older])).toEqual([newer]);
  });

  it('summarizes active and terminal states for future batch controls', () => {
    const slots = getRuntimeMediaImageSlots([
      { ...slot(0), slot_status: 'completed', url: '/done.png' },
      { ...slot(1), slot_status: 'failed' },
      { ...slot(2), slot_status: 'cancelled' },
      { ...slot(3), slot_status: 'accepted' },
      { ...slot(4), slot_status: 'unknown' },
    ]);

    expect(summarizeRuntimeMediaSlots(slots)).toMatchObject({
      total: 5, completed: 1, failed: 1, cancelled: 1, accepted: 1,
      unknown: 1, active: 2,
    });
  });

  it('locates legacy image_index inside the image collection, not mixed content', () => {
    const content: ContentPart[] = [
      { type: 'text', text: 'before' },
      { type: 'image', url: '/zero.png' },
      { type: 'text', text: 'after' },
      { type: 'image', url: null },
    ];

    expect(findImagePartContentIndex(content, 1)).toBe(3);
  });
});
