import { describe, expect, it } from 'vitest';

import type { Message } from '../../types/message';
import { mergeMessages } from '../useUnifiedMessages';


function message(
  id: string,
  createdAt: string,
  role: Message['role'] = 'assistant',
): Message {
  return {
    id,
    conversation_id: 'conv_1',
    role,
    content: [{ type: 'text', text: id }],
    status: 'completed',
    created_at: createdAt,
  };
}


describe('mergeMessages', () => {
  it('keeps send order when persisted results arrive in completion order', () => {
    const result = mergeMessages([
      message('second-task', '2026-07-27T08:01:00Z'),
      message('first-task', '2026-07-27T08:00:00Z'),
    ], []);

    expect(result.map((item) => item.id)).toEqual(['first-task', 'second-task']);
  });

  it('deduplicates optimistic placeholders and keeps their original order', () => {
    const first = message('first-task', '2026-07-27T08:00:00Z');
    const second = message('second-task', '2026-07-27T08:01:00Z');

    const result = mergeMessages([second, first], [first, second]);

    expect(result.map((item) => item.id)).toEqual(['first-task', 'second-task']);
  });

  it('keeps each result beside its user request when tasks finish out of order', () => {
    const result = mergeMessages([
      message('user-a', '2026-07-27T08:00:00.000Z', 'user'),
      message('user-b', '2026-07-27T08:01:00.000Z', 'user'),
      message('result-b', '2026-07-27T08:01:00.001Z'),
      message('result-a', '2026-07-27T08:00:00.001Z'),
    ], []);

    expect(result.map((item) => item.id)).toEqual([
      'user-a', 'result-a', 'user-b', 'result-b',
    ]);
  });
});
