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
        id: 'msg_1',
        conversation_id: 'conv_1',
        role: 'assistant',
        content: [{ type: 'text', text: '回复' }],
        status: 'completed',
        created_at: new Date().toISOString(),
      });

      expect(store.getState().suggestions.get('conv_1')).toBeUndefined();
    });

    it('should preserve another message streaming in the same conversation', () => {
      const state = store.getState();
      state.startStreaming('conv_1', 'msg_a', {
        createdAt: '2026-07-27T08:00:00.001Z',
      });
      state.startStreaming('conv_1', 'msg_b', {
        createdAt: '2026-07-27T08:01:00.001Z',
      });
      state.setSuggestions('conv_1', ['建议']);

      store.getState().completeStreamingWithMessage('conv_1', {
        id: 'msg_a',
        conversation_id: 'conv_1',
        role: 'assistant',
        content: [{ type: 'image', url: 'https://example.com/a.png' }],
        status: 'completed',
        created_at: '2026-07-27T09:00:00.000Z',
      });

      const messages = store.getState().optimisticMessages.get('conv_1')!;
      expect(messages.map((message) => message.id)).toEqual(['msg_a', 'msg_b']);
      expect(messages[0].created_at).toBe('2026-07-27T08:00:00.001Z');
      expect(messages[0].content).toEqual([
        { type: 'image', url: 'https://example.com/a.png' },
      ]);
      expect(store.getState().getStreamingMessageId('conv_1')).toBe('msg_b');
      expect(store.getState().suggestions.get('conv_1')).toEqual(['建议']);
    });
  });

  describe('updateContentBlock', () => {
    it('should update matching tool_step block by tool_call_id', () => {
      const state = store.getState();
      state.startStreaming('conv_1', 'msg_1');
      // 追加一个 running tool_step
      state.appendContentBlock('conv_1', {
        type: 'tool_step',
        tool_name: 'web_search',
        tool_call_id: 'tc_1',
        status: 'running',
      });

      // 更新为 completed
      store.getState().updateContentBlock('conv_1', 'tc_1', {
        status: 'completed',
        summary: '找到3条结果',
        elapsed_ms: 1500,
      });

      const msgs = store.getState().optimisticMessages.get('conv_1')!;
      const block = msgs[msgs.length - 1].content.find(
        (b: Record<string, unknown>) => b.type === 'tool_step',
      ) as Record<string, unknown>;
      expect(block.status).toBe('completed');
      expect(block.summary).toBe('找到3条结果');
      expect(block.elapsed_ms).toBe(1500);
    });

    it('should not modify other blocks when updating', () => {
      const state = store.getState();
      state.startStreaming('conv_1', 'msg_1');
      state.appendContentBlock('conv_1', {
        type: 'tool_step', tool_name: 'a', tool_call_id: 'tc_a', status: 'running',
      });
      state.appendContentBlock('conv_1', {
        type: 'tool_step', tool_name: 'b', tool_call_id: 'tc_b', status: 'running',
      });

      store.getState().updateContentBlock('conv_1', 'tc_b', { status: 'completed' });

      const msgs = store.getState().optimisticMessages.get('conv_1')!;
      const blocks = msgs[msgs.length - 1].content.filter(
        (b: Record<string, unknown>) => b.type === 'tool_step',
      ) as Array<Record<string, unknown>>;
      expect(blocks[0].status).toBe('running');   // tc_a 不变
      expect(blocks[1].status).toBe('completed'); // tc_b 更新
    });

    it('should be no-op when conversation has no streaming message', () => {
      // 不应抛异常
      store.getState().updateContentBlock('nonexistent', 'tc_1', { status: 'completed' });
    });

    it('should be no-op when tool_call_id does not match', () => {
      const state = store.getState();
      state.startStreaming('conv_1', 'msg_1');
      state.appendContentBlock('conv_1', {
        type: 'tool_step', tool_name: 'a', tool_call_id: 'tc_a', status: 'running',
      });

      store.getState().updateContentBlock('conv_1', 'tc_nonexistent', { status: 'completed' });

      const msgs = store.getState().optimisticMessages.get('conv_1')!;
      const block = msgs[msgs.length - 1].content.find(
        (b: Record<string, unknown>) => b.type === 'tool_step',
      ) as Record<string, unknown>;
      expect(block.status).toBe('running'); // 未匹配，不变
    });

    it('should insert a complete tool block when terminal event arrives first', () => {
      const state = store.getState();
      state.startStreaming('conv_1', 'msg_1');

      state.updateContentBlock('conv_1', 'tc_terminal', {
        type: 'tool_step',
        tool_name: 'erp_agent',
        tool_call_id: 'tc_terminal',
        status: 'completed',
        output: 'done',
      });

      const msgs = store.getState().optimisticMessages.get('conv_1')!;
      expect(msgs[0].content[1]).toEqual({
        type: 'tool_step',
        tool_name: 'erp_agent',
        tool_call_id: 'tc_terminal',
        status: 'completed',
        output: 'done',
      });
    });

    it('should create a streaming placeholder when binding is restored first', () => {
      store.getState().registerStreamingId('conv_1', 'restored_msg');

      expect(store.getState().streamingMessages.get('conv_1')).toBe('restored_msg');
      expect(store.getState().optimisticMessages.get('conv_1')).toEqual([
        expect.objectContaining({
          id: 'restored_msg',
          role: 'assistant',
          status: 'streaming',
          content: [],
        }),
      ]);
    });
  });
});
