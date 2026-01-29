/**
 * mergeOptimisticMessages 工具函数单元测试
 */

import { describe, it, expect } from 'vitest';
import { mergeOptimisticMessages } from '../mergeOptimisticMessages';
import type { Message } from '../../services/message';
import type { RuntimeState } from '../mergeOptimisticMessages';

// 辅助函数：创建测试消息
function createMessage(overrides: Partial<Message> = {}): Message {
  return {
    id: 'msg-1',
    conversation_id: 'conv-1',
    role: 'user',
    content: 'test message',
    created_at: new Date().toISOString(),
    ...overrides,
  };
}

describe('mergeOptimisticMessages', () => {
  it('should return persisted messages when no runtime state', () => {
    const persistedMessages = [
      createMessage({ id: 'msg-1', content: 'Message 1' }),
      createMessage({ id: 'msg-2', content: 'Message 2' }),
    ];

    const result = mergeOptimisticMessages(persistedMessages, undefined);

    expect(result).toEqual(persistedMessages);
  });

  it('should return persisted messages when optimistic messages array is empty', () => {
    const persistedMessages = [createMessage({ id: 'msg-1' })];
    const runtimeState: RuntimeState = {
      streamingMessageId: null,
      optimisticMessages: [],
    };

    const result = mergeOptimisticMessages(persistedMessages, runtimeState);

    expect(result).toEqual(persistedMessages);
  });

  it('should filter out optimistic messages already in persisted messages', () => {
    const persistedMessages = [
      createMessage({ id: 'msg-1', content: 'Message 1' }),
    ];

    const runtimeState: RuntimeState = {
      streamingMessageId: null,
      optimisticMessages: [
        createMessage({ id: 'msg-1', content: 'Message 1' }), // Already persisted
        createMessage({ id: 'msg-2', content: 'Message 2' }), // New
      ],
    };

    const result = mergeOptimisticMessages(persistedMessages, runtimeState);

    expect(result).toHaveLength(2);
    expect(result[0].id).toBe('msg-1');
    expect(result[1].id).toBe('msg-2');
  });

  it('should filter out temp- user messages when content exists in persisted messages', () => {
    const persistedMessages = [
      createMessage({ id: 'msg-real', role: 'user', content: 'Hello' }),
    ];

    const runtimeState: RuntimeState = {
      streamingMessageId: null,
      optimisticMessages: [
        createMessage({ id: 'temp-123', role: 'user', content: 'Hello' }), // Same content as persisted
      ],
    };

    const result = mergeOptimisticMessages(persistedMessages, runtimeState);

    // Should only have the persisted message
    expect(result).toHaveLength(1);
    expect(result[0].id).toBe('msg-real');
  });

  it('should keep temp- user messages when content does not exist in persisted messages', () => {
    const persistedMessages = [
      createMessage({ id: 'msg-1', role: 'user', content: 'Hello' }),
    ];

    const runtimeState: RuntimeState = {
      streamingMessageId: null,
      optimisticMessages: [
        createMessage({ id: 'temp-123', role: 'user', content: 'World' }), // Different content
      ],
    };

    const result = mergeOptimisticMessages(persistedMessages, runtimeState);

    expect(result).toHaveLength(2);
    expect(result.some((m) => m.id === 'temp-123')).toBe(true);
  });

  it('should keep streaming- message when it is the current streaming message', () => {
    const persistedMessages: Message[] = [];

    const runtimeState: RuntimeState = {
      streamingMessageId: 'streaming-123',
      optimisticMessages: [
        createMessage({ id: 'streaming-123', role: 'assistant', content: 'Generating...' }),
      ],
    };

    const result = mergeOptimisticMessages(persistedMessages, runtimeState);

    expect(result).toHaveLength(1);
    expect(result[0].id).toBe('streaming-123');
  });

  it('should keep media placeholder messages (图片生成中)', () => {
    const persistedMessages: Message[] = [];

    const runtimeState: RuntimeState = {
      streamingMessageId: null,
      optimisticMessages: [
        createMessage({ id: 'streaming-img', role: 'assistant', content: '图片生成中...' }),
      ],
    };

    const result = mergeOptimisticMessages(persistedMessages, runtimeState);

    expect(result).toHaveLength(1);
    expect(result[0].id).toBe('streaming-img');
  });

  it('should keep media placeholder messages (视频生成中)', () => {
    const persistedMessages: Message[] = [];

    const runtimeState: RuntimeState = {
      streamingMessageId: null,
      optimisticMessages: [
        createMessage({ id: 'streaming-video', role: 'assistant', content: '视频生成中...' }),
      ],
    };

    const result = mergeOptimisticMessages(persistedMessages, runtimeState);

    expect(result).toHaveLength(1);
    expect(result[0].id).toBe('streaming-video');
  });

  it('should filter out completed streaming messages when content exists in persisted messages', () => {
    const persistedMessages = [
      createMessage({ id: 'msg-real', role: 'assistant', content: 'Hello, how can I help?' }),
    ];

    const runtimeState: RuntimeState = {
      streamingMessageId: null,
      optimisticMessages: [
        createMessage({
          id: 'streaming-old',
          role: 'assistant',
          content: 'Hello, how can I help?',
        }), // Same content as persisted
      ],
    };

    const result = mergeOptimisticMessages(persistedMessages, runtimeState);

    // Should only have the persisted message
    expect(result).toHaveLength(1);
    expect(result[0].id).toBe('msg-real');
  });

  it('should keep completed streaming messages when content does not exist in persisted messages', () => {
    const persistedMessages = [
      createMessage({ id: 'msg-1', role: 'assistant', content: 'Hello' }),
    ];

    const runtimeState: RuntimeState = {
      streamingMessageId: null,
      optimisticMessages: [
        createMessage({ id: 'streaming-123', role: 'assistant', content: 'World' }), // Different content
      ],
    };

    const result = mergeOptimisticMessages(persistedMessages, runtimeState);

    expect(result).toHaveLength(2);
    expect(result.some((m) => m.id === 'streaming-123')).toBe(true);
  });

  it('should sort merged messages by created_at time', () => {
    const persistedMessages = [
      createMessage({ id: 'msg-2', created_at: '2024-01-01T12:00:00Z' }),
    ];

    const runtimeState: RuntimeState = {
      streamingMessageId: null,
      optimisticMessages: [
        createMessage({ id: 'msg-1', created_at: '2024-01-01T10:00:00Z' }), // Earlier
        createMessage({ id: 'msg-3', created_at: '2024-01-01T14:00:00Z' }), // Later
      ],
    };

    const result = mergeOptimisticMessages(persistedMessages, runtimeState);

    expect(result).toHaveLength(3);
    expect(result[0].id).toBe('msg-1'); // Earliest
    expect(result[1].id).toBe('msg-2');
    expect(result[2].id).toBe('msg-3'); // Latest
  });

  it('should handle complex scenario with all message types', () => {
    const persistedMessages = [
      createMessage({ id: 'msg-1', role: 'user', content: 'Hello', created_at: '2024-01-01T10:00:00Z' }),
      createMessage({ id: 'msg-2', role: 'assistant', content: 'Hi there', created_at: '2024-01-01T10:01:00Z' }),
    ];

    const runtimeState: RuntimeState = {
      streamingMessageId: 'streaming-current',
      optimisticMessages: [
        createMessage({ id: 'msg-1', role: 'user', content: 'Hello' }), // Already persisted (should be filtered)
        createMessage({ id: 'temp-new', role: 'user', content: 'New question', created_at: '2024-01-01T10:02:00Z' }), // New temp message (should be kept)
        createMessage({ id: 'streaming-current', role: 'assistant', content: 'Typing...', created_at: '2024-01-01T10:03:00Z' }), // Current streaming (should be kept)
        createMessage({ id: 'streaming-img', role: 'assistant', content: '图片生成中', created_at: '2024-01-01T10:04:00Z' }), // Media placeholder (should be kept)
      ],
    };

    const result = mergeOptimisticMessages(persistedMessages, runtimeState);

    expect(result).toHaveLength(5); // 2 persisted + 3 optimistic
    expect(result[0].id).toBe('msg-1');
    expect(result[1].id).toBe('msg-2');
    expect(result[2].id).toBe('temp-new');
    expect(result[3].id).toBe('streaming-current');
    expect(result[4].id).toBe('streaming-img');
  });
});
