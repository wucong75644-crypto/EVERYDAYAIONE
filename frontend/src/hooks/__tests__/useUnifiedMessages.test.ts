import { describe, expect, it } from 'vitest';
import { mergeMessages } from '../useUnifiedMessages';

const baseMessage = {
  id: 'assistant-1',
  conversation_id: 'conversation-1',
  role: 'assistant' as const,
  content: [],
  created_at: '2026-08-23T00:00:00.000Z',
};

describe('mergeMessages', () => {
  it('keeps restored partial content when the persisted placeholder has the same id', () => {
    const result = mergeMessages(
      [{ ...baseMessage, status: 'streaming' as const }],
      [{
        ...baseMessage,
        status: 'streaming' as const,
        content: [{ type: 'text' as const, text: '已经生成的部分' }],
      }],
    );

    expect(result).toHaveLength(1);
    expect(result[0].content).toEqual([
      { type: 'text', text: '已经生成的部分' },
    ]);
  });

  it('does not let a stale streaming placeholder overwrite a completed message', () => {
    const completed = {
      ...baseMessage,
      status: 'completed' as const,
      content: [{ type: 'text' as const, text: '最终结果' }],
    };

    const result = mergeMessages(
      [completed],
      [{ ...baseMessage, status: 'streaming' as const }],
    );

    expect(result[0]).toEqual(completed);
  });
});
