/**
 * StreamingSlice 思考内容方法单元测试
 *
 * 覆盖：appendStreamingThinking 追加、getStreamingThinking 查询、清理逻辑
 */

import { describe, it, expect, beforeEach } from 'vitest';
import { create } from 'zustand';
import { createStreamingSlice, type StreamingSlice, type StreamingSliceDeps } from '../streamingSlice';

type TestStore = StreamingSlice & StreamingSliceDeps;

function createTestStore() {
  return create<TestStore>()((set, get, api) => ({
    messages: {},
    ...createStreamingSlice(set, get, api),
  }));
}

describe('streamingSlice - thinking methods', () => {
  let store: ReturnType<typeof createTestStore>;

  beforeEach(() => {
    store = createTestStore();
  });

  describe('appendStreamingThinking', () => {
    it('should append chunk to empty thinking', () => {
      store.getState().appendStreamingThinking('conv_1', '思考');
      expect(store.getState().streamingThinking.get('conv_1')).toBe('思考');
    });

    it('should accumulate multiple chunks', () => {
      const { appendStreamingThinking } = store.getState();
      appendStreamingThinking('conv_1', '第一');
      appendStreamingThinking('conv_1', '第二');
      appendStreamingThinking('conv_1', '第三');

      expect(store.getState().streamingThinking.get('conv_1')).toBe('第一第二第三');
    });

    it('should maintain separate thinking per conversation', () => {
      const { appendStreamingThinking } = store.getState();
      appendStreamingThinking('conv_a', 'A的思考');
      appendStreamingThinking('conv_b', 'B的思考');

      expect(store.getState().streamingThinking.get('conv_a')).toBe('A的思考');
      expect(store.getState().streamingThinking.get('conv_b')).toBe('B的思考');
    });
  });

  describe('getStreamingThinking', () => {
    it('should return accumulated thinking for existing conversation', () => {
      store.getState().appendStreamingThinking('conv_1', '内容');
      expect(store.getState().getStreamingThinking('conv_1')).toBe('内容');
    });

    it('should return empty string for unknown conversation', () => {
      expect(store.getState().getStreamingThinking('nonexistent')).toBe('');
    });
  });

  describe('completeStreaming clears thinking', () => {
    it('should clear streamingThinking on completeStreaming', () => {
      const state = store.getState();
      // Setup: start streaming and add thinking
      state.startStreaming('conv_1', 'msg_1');
      state.appendStreamingThinking('conv_1', '思考中');

      expect(store.getState().streamingThinking.get('conv_1')).toBe('思考中');

      // Complete streaming should clear thinking
      store.getState().completeStreaming('conv_1');

      expect(store.getState().streamingThinking.get('conv_1')).toBeUndefined();
    });
  });
});

// ============================================================
// suggestions 相关测试
// ============================================================

describe('streamingSlice - suggestions', () => {
  let store: ReturnType<typeof createTestStore>;

  beforeEach(() => {
    store = createTestStore();
  });

  describe('setSuggestions', () => {
    it('should set suggestions for a conversation', () => {
      store.getState().setSuggestions('conv_1', ['建议一', '建议二']);
      expect(store.getState().suggestions.get('conv_1')).toEqual(['建议一', '建议二']);
    });

    it('should overwrite existing suggestions', () => {
      store.getState().setSuggestions('conv_1', ['旧建议']);
      store.getState().setSuggestions('conv_1', ['新建议']);
      expect(store.getState().suggestions.get('conv_1')).toEqual(['新建议']);
    });

    it('should maintain separate suggestions per conversation', () => {
      store.getState().setSuggestions('conv_a', ['A建议']);
      store.getState().setSuggestions('conv_b', ['B建议']);
      expect(store.getState().suggestions.get('conv_a')).toEqual(['A建议']);
      expect(store.getState().suggestions.get('conv_b')).toEqual(['B建议']);
    });
  });

  describe('clearSuggestions', () => {
    it('should clear suggestions for a conversation', () => {
      store.getState().setSuggestions('conv_1', ['建议']);
      store.getState().clearSuggestions('conv_1');
      expect(store.getState().suggestions.get('conv_1')).toBeUndefined();
    });

    it('should not affect other conversations', () => {
      store.getState().setSuggestions('conv_a', ['A']);
      store.getState().setSuggestions('conv_b', ['B']);
      store.getState().clearSuggestions('conv_a');
      expect(store.getState().suggestions.get('conv_a')).toBeUndefined();
      expect(store.getState().suggestions.get('conv_b')).toEqual(['B']);
    });
  });

  describe('completeStreaming clears suggestions', () => {
    it('should clear suggestions on completeStreaming', () => {
      const state = store.getState();
      state.startStreaming('conv_1', 'msg_1');
      state.setSuggestions('conv_1', ['建议']);

      store.getState().completeStreaming('conv_1');

      expect(store.getState().suggestions.get('conv_1')).toBeUndefined();
    });

    it('should clear suggestions on completeStreamingWithMessage', () => {
      const state = store.getState();
      state.startStreaming('conv_1', 'msg_1');
      state.setSuggestions('conv_1', ['建议']);

      store.getState().completeStreamingWithMessage('conv_1', {
        id: 'msg_done',
        conversation_id: 'conv_1',
        role: 'assistant',
        content: [{ type: 'text', text: '回复' }],
        status: 'completed',
        created_at: new Date().toISOString(),
      });

      expect(store.getState().suggestions.get('conv_1')).toBeUndefined();
    });
  });
});
