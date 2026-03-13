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
