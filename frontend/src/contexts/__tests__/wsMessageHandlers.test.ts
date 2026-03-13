/**
 * wsMessageHandlers 单元测试
 *
 * 测试从 WebSocketContext 提取的纯函数 handler 工厂。
 * 由于是纯函数，不依赖 React，可以直接测试。
 *
 * 覆盖：
 * 1. createWSMessageHandlers 返回 8 个 handler
 * 2. message_start: 设置消息状态为 streaming
 * 3. message_chunk: 缓冲机制 + 流式回调
 * 4. message_progress: 更新任务进度
 * 5. message_done: 任务完成 + 幂等检查 + 回调
 * 6. message_error: 错误处理 + 缓冲清理
 * 7. credits_changed: 积分更新
 * 8. flushChunkBuffer: 批量刷新
 * 9. 边界情况
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { createWSMessageHandlers, flushChunkBuffer, type HandlerDeps, type MessageStoreActions } from '../wsMessageHandlers';

// Mock 外部依赖
vi.mock('../../stores/useMessageStore', () => ({
  useMessageStore: vi.fn(),
  normalizeMessage: (msg: any) => ({
    ...msg,
    content: Array.isArray(msg.content) ? msg.content : [],
    status: msg.status || 'completed',
  }),
}));

const mockSetUser = vi.fn();
const mockAuthStore = {
  user: { id: 'user_123', credits: 100 } as any,
  setUser: mockSetUser,
};

vi.mock('../../stores/useAuthStore', () => ({
  useAuthStore: {
    getState: () => mockAuthStore,
  },
}));

vi.mock('../../utils/logger', () => ({
  logger: {
    info: vi.fn(),
    debug: vi.fn(),
    warn: vi.fn(),
    error: vi.fn(),
  },
}));

vi.mock('../../utils/tabSync', () => ({
  tabSync: { broadcast: vi.fn() },
}));

vi.mock('react-hot-toast', () => ({
  default: { success: vi.fn(), error: vi.fn() },
}));

// ============================================================
// 测试辅助
// ============================================================

function createMockStore(): MessageStoreActions {
  return {
    setStatus: vi.fn(),
    appendStreamingContent: vi.fn(),
    appendContent: vi.fn(),
    updateTaskProgress: vi.fn(),
    updateMessage: vi.fn(),
    addMessage: vi.fn(),
    completeTask: vi.fn(),
    failTask: vi.fn(),
    completeStreaming: vi.fn(),
    completeStreamingWithMessage: vi.fn(),
    markConversationCompleted: vi.fn(),
    setIsSending: vi.fn(),
    getMessage: vi.fn(),
    setStreamingContent: vi.fn(),
    setAgentStepHint: vi.fn(),
    clearAgentStepHint: vi.fn(),
    appendStreamingThinking: vi.fn(),
  };
}

function createMockDeps(store: MessageStoreActions): HandlerDeps {
  return {
    getStore: () => store,
    subscribedTasksRef: { current: new Set<string>() },
    taskConversationMapRef: { current: new Map<string, string>() },
    operationContextRef: { current: new Map() },
    chunkBufferRef: { current: new Map() },
    flushTimerRef: { current: null },
    unsubscribeTask: vi.fn(),
  };
}

// ============================================================
// 测试套件
// ============================================================

describe('wsMessageHandlers', () => {
  let store: MessageStoreActions;
  let deps: HandlerDeps;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  let handlers: Record<string, (msg: any) => void>;

  beforeEach(() => {
    vi.useFakeTimers();
    store = createMockStore();
    deps = createMockDeps(store);
    handlers = createWSMessageHandlers(deps);
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.clearAllMocks();
  });

  // ========================================
  // 1. 工厂函数基本验证
  // ========================================

  describe('createWSMessageHandlers', () => {
    it('should return all 9 handlers', () => {
      const expectedTypes = [
        'message_start', 'message_chunk', 'message_progress',
        'message_done', 'message_error', 'image_partial_update',
        'credits_changed', 'subscribed', 'error',
      ];

      for (const type of expectedTypes) {
        expect(handlers[type]).toBeDefined();
        expect(typeof handlers[type]).toBe('function');
      }
    });
  });

  // ========================================
  // 2. message_start
  // ========================================

  describe('message_start', () => {
    it('should set status to streaming', () => {
      handlers.message_start({ message_id: 'msg_1' });
      expect(store.setStatus).toHaveBeenCalledWith('msg_1', 'streaming');
    });

    it('should ignore messages without message_id', () => {
      handlers.message_start({});
      expect(store.setStatus).not.toHaveBeenCalled();
    });
  });

  // ========================================
  // 3. message_chunk
  // ========================================

  describe('message_chunk', () => {
    it('should immediately flush first chunk and buffer subsequent ones', () => {
      handlers.message_chunk({
        message_id: 'msg_1',
        conversation_id: 'conv_1',
        chunk: 'Hello ',
      });

      // 首字节立即 flush（不再缓冲）
      expect(store.appendStreamingContent).toHaveBeenCalledWith('conv_1', 'Hello ');

      // flush 后 buffer 保留空标记（防止后续 chunk 被当首字节）
      expect(deps.chunkBufferRef.current.size).toBe(1);
      expect(deps.chunkBufferRef.current.get('msg_1')?.chunk).toBe('');
    });

    it('should accumulate subsequent chunks in buffer', () => {
      handlers.message_chunk({
        message_id: 'msg_1',
        conversation_id: 'conv_1',
        chunk: 'Hello ',
      });

      // 首字节立即 flush
      expect(store.appendStreamingContent).toHaveBeenCalledTimes(1);
      store.appendStreamingContent.mockClear();

      // flush 后 buffer 中留有空标记（防止后续 chunk 被当首字节）
      expect(deps.chunkBufferRef.current.get('msg_1')?.chunk).toBe('');

      handlers.message_chunk({
        message_id: 'msg_1',
        conversation_id: 'conv_1',
        chunk: 'World',
      });

      // 第二个 chunk 被缓冲（不立即 flush）
      expect(store.appendStreamingContent).not.toHaveBeenCalled();
      expect(deps.chunkBufferRef.current.get('msg_1')?.chunk).toBe('World');
    });

    it('should trigger onStreamChunk callback immediately', () => {
      const onStreamChunk = vi.fn();
      deps.operationContextRef.current.set('task_1', {
        type: 'chat',
        operation: 'send',
        conversationId: 'conv_1',
        onStreamChunk,
      });

      handlers.message_chunk({
        message_id: 'msg_1',
        task_id: 'task_1',
        conversation_id: 'conv_1',
        chunk: 'Test',
      });

      expect(onStreamChunk).toHaveBeenCalledWith('Test', 'Test');
    });

    it('should flush subsequent chunks after 16ms', () => {
      // 首字节立即 flush
      handlers.message_chunk({
        message_id: 'msg_1',
        conversation_id: 'conv_1',
        chunk: 'Hello',
      });
      expect(store.appendStreamingContent).toHaveBeenCalledWith('conv_1', 'Hello');
      store.appendStreamingContent.mockClear();

      // 后续 chunk 被缓冲
      handlers.message_chunk({
        message_id: 'msg_1',
        conversation_id: 'conv_1',
        chunk: ' World',
      });

      // 16ms 后 flush
      vi.advanceTimersByTime(16);

      expect(store.appendStreamingContent).toHaveBeenCalledWith('conv_1', ' World');
      expect(deps.chunkBufferRef.current.size).toBe(0);
    });

    it('should ignore chunk without conversation_id', () => {
      handlers.message_chunk({
        message_id: 'msg_1',
        chunk: 'Test',
      });

      expect(deps.chunkBufferRef.current.size).toBe(0);
    });
  });

  // ========================================
  // 4. message_progress
  // ========================================

  describe('message_progress', () => {
    it('should update task progress', () => {
      handlers.message_progress({ task_id: 'task_1', progress: 50 });
      expect(store.updateTaskProgress).toHaveBeenCalledWith('task_1', 50);
    });

    it('should ignore without task_id', () => {
      handlers.message_progress({ progress: 50 });
      expect(store.updateTaskProgress).not.toHaveBeenCalled();
    });
  });

  // ========================================
  // 5. message_done
  // ========================================

  describe('message_done', () => {
    it('should complete task with message data', () => {
      (store.getMessage as ReturnType<typeof vi.fn>).mockReturnValue(null);

      handlers.message_done({
        task_id: 'task_1',
        conversation_id: 'conv_1',
        message: {
          id: 'msg_1',
          role: 'assistant',
          content: [{ type: 'text', text: 'Done' }],
          created_at: new Date().toISOString(),
        },
      });

      expect(store.updateMessage).toHaveBeenCalledWith(
        'msg_1',
        expect.objectContaining({ status: 'completed' })
      );
      expect(store.addMessage).toHaveBeenCalledWith('conv_1', expect.objectContaining({ id: 'msg_1' }));
      expect(store.completeTask).toHaveBeenCalledWith('task_1');
      expect(store.completeStreaming).toHaveBeenCalledWith('conv_1');
      expect(store.markConversationCompleted).toHaveBeenCalledWith('conv_1');
      expect(store.setIsSending).toHaveBeenCalledWith(false);
    });

    it('should skip if message already completed (idempotency)', () => {
      (store.getMessage as ReturnType<typeof vi.fn>).mockReturnValue({
        id: 'msg_1',
        status: 'completed',
      });

      handlers.message_done({
        task_id: 'task_1',
        conversation_id: 'conv_1',
        message: {
          id: 'msg_1',
          role: 'assistant',
          content: [],
          created_at: new Date().toISOString(),
        },
      });

      // updateMessage should NOT be called (idempotent skip)
      expect(store.updateMessage).not.toHaveBeenCalled();
    });

    it('should trigger onComplete callback', () => {
      const onComplete = vi.fn();
      deps.operationContextRef.current.set('task_1', {
        type: 'chat',
        operation: 'send',
        conversationId: 'conv_1',
        onComplete,
      });

      (store.getMessage as ReturnType<typeof vi.fn>).mockReturnValue(null);

      handlers.message_done({
        task_id: 'task_1',
        conversation_id: 'conv_1',
        message: {
          id: 'msg_1',
          role: 'assistant',
          content: [],
          created_at: new Date().toISOString(),
        },
      });

      expect(onComplete).toHaveBeenCalledWith(expect.objectContaining({ id: 'msg_1' }));
      // context should be cleaned up
      expect(deps.operationContextRef.current.has('task_1')).toBe(false);
    });

    it('should flush chunk buffer before completing', () => {
      // Pre-fill buffer
      deps.chunkBufferRef.current.set('msg_1', { chunk: 'buffered', conversationId: 'conv_1' });

      (store.getMessage as ReturnType<typeof vi.fn>).mockReturnValue(null);

      handlers.message_done({
        task_id: 'task_1',
        conversation_id: 'conv_1',
        message: {
          id: 'msg_1',
          role: 'assistant',
          content: [],
          created_at: new Date().toISOString(),
        },
      });

      // Buffer should have been flushed
      expect(store.appendStreamingContent).toHaveBeenCalledWith('conv_1', 'buffered');
      expect(deps.chunkBufferRef.current.size).toBe(0);
    });

    it('should handle message_done with only message_id (no task)', () => {
      handlers.message_done({
        message_id: 'msg_1',
      });

      expect(store.setStatus).toHaveBeenCalledWith('msg_1', 'completed');
    });

    it('should fallback to taskConversationMap for conversationId', () => {
      deps.taskConversationMapRef.current.set('task_1', 'conv_mapped');

      (store.getMessage as ReturnType<typeof vi.fn>).mockReturnValue(null);

      handlers.message_done({
        task_id: 'task_1',
        // no conversation_id
        message: {
          id: 'msg_1',
          role: 'assistant',
          content: [],
          created_at: new Date().toISOString(),
        },
      });

      expect(store.completeStreaming).toHaveBeenCalledWith('conv_mapped');
    });

    it('should cleanup task subscription on done', () => {
      deps.subscribedTasksRef.current.add('task_1');
      deps.taskConversationMapRef.current.set('task_1', 'conv_1');

      handlers.message_done({
        task_id: 'task_1',
        message_id: 'msg_1',
      });

      expect(deps.subscribedTasksRef.current.has('task_1')).toBe(false);
      expect(deps.taskConversationMapRef.current.has('task_1')).toBe(false);
      expect(deps.unsubscribeTask).toHaveBeenCalledWith('task_1');
    });
  });

  // ========================================
  // 6. message_error
  // ========================================

  describe('message_error', () => {
    it('should update message status to failed', () => {
      handlers.message_error({
        task_id: 'task_1',
        message_id: 'msg_1',
        conversation_id: 'conv_1',
        error: { code: 'TEST', message: '测试错误' },
      });

      expect(store.updateMessage).toHaveBeenCalledWith('msg_1', expect.objectContaining({
        status: 'failed',
        is_error: true,
        content: [{ type: 'text', text: '测试错误' }],
      }));
      expect(store.failTask).toHaveBeenCalledWith('task_1', '测试错误');
      expect(store.completeStreaming).toHaveBeenCalledWith('conv_1');
      expect(store.setIsSending).toHaveBeenCalledWith(false);
    });

    it('should clear chunk buffer for failed message', () => {
      deps.chunkBufferRef.current.set('msg_1', { chunk: 'stale', conversationId: 'conv_1' });

      handlers.message_error({
        message_id: 'msg_1',
        task_id: 'task_1',
        error: { message: 'Error' },
      });

      expect(deps.chunkBufferRef.current.has('msg_1')).toBe(false);
    });

    it('should trigger onError callback', () => {
      const onError = vi.fn();
      deps.operationContextRef.current.set('task_1', {
        type: 'chat',
        operation: 'send',
        conversationId: 'conv_1',
        onError,
      });

      handlers.message_error({
        task_id: 'task_1',
        message_id: 'msg_1',
        error: { message: '测试' },
      });

      expect(onError).toHaveBeenCalledWith(expect.any(Error));
      expect(onError.mock.calls[0][0].message).toBe('测试');
      // context should be cleaned up
      expect(deps.operationContextRef.current.has('task_1')).toBe(false);
    });

    it('should use default error message when error is missing', () => {
      handlers.message_error({
        message_id: 'msg_1',
      });

      expect(store.updateMessage).toHaveBeenCalledWith('msg_1', expect.objectContaining({
        content: [{ type: 'text', text: '生成失败' }],
      }));
    });
  });

  // ========================================
  // 7. credits_changed
  // ========================================

  describe('credits_changed', () => {
    beforeEach(() => {
      mockSetUser.mockClear();
      mockAuthStore.user = { id: 'user_123', credits: 100 };
    });

    it('should update user credits', () => {
      handlers.credits_changed({ credits: 200 });

      expect(mockSetUser).toHaveBeenCalledWith(expect.objectContaining({ credits: 200 }));
    });

    it('should ignore if no user', () => {
      mockAuthStore.user = null;

      handlers.credits_changed({ credits: 200 });

      expect(mockSetUser).not.toHaveBeenCalled();
    });

    it('should ignore if credits is undefined', () => {
      handlers.credits_changed({});

      expect(mockSetUser).not.toHaveBeenCalled();
    });
  });

  // ========================================
  // 8. subscribed
  // ========================================

  describe('subscribed', () => {
    it('should set streaming content from accumulated', () => {
      deps.taskConversationMapRef.current.set('task_1', 'conv_1');

      handlers.subscribed({
        payload: { task_id: 'task_1', accumulated: 'Accumulated text' },
      });

      expect(store.setStreamingContent).toHaveBeenCalledWith('conv_1', 'Accumulated text');
    });

    it('should not set content if no task mapping', () => {
      handlers.subscribed({
        payload: { task_id: 'unknown', accumulated: 'Text' },
      });

      expect(store.setStreamingContent).not.toHaveBeenCalled();
    });

    it('should not set content if accumulated is empty', () => {
      deps.taskConversationMapRef.current.set('task_1', 'conv_1');

      handlers.subscribed({
        payload: { task_id: 'task_1', accumulated: '' },
      });

      expect(store.setStreamingContent).not.toHaveBeenCalled();
    });
  });

  // ========================================
  // 9. flushChunkBuffer
  // ========================================

  describe('flushChunkBuffer', () => {
    it('should flush all buffered chunks to store', () => {
      deps.chunkBufferRef.current.set('msg_1', { chunk: 'Hello', conversationId: 'conv_1' });
      deps.chunkBufferRef.current.set('msg_2', { chunk: 'World', conversationId: 'conv_2' });

      flushChunkBuffer(deps);

      expect(store.appendStreamingContent).toHaveBeenCalledWith('conv_1', 'Hello');
      expect(store.appendStreamingContent).toHaveBeenCalledWith('conv_2', 'World');
      expect(deps.chunkBufferRef.current.size).toBe(0);
    });

    it('should do nothing on empty buffer', () => {
      flushChunkBuffer(deps);
      expect(store.appendStreamingContent).not.toHaveBeenCalled();
    });

    it('should use appendContent fallback when no conversationId', () => {
      deps.chunkBufferRef.current.set('msg_1', { chunk: 'Test', conversationId: '' });

      flushChunkBuffer(deps);

      expect(store.appendContent).toHaveBeenCalledWith('msg_1', 'Test');
    });
  });

  // ========================================
  // 10. image_partial_update
  // ========================================

  describe('image_partial_update', () => {
    it('should update message content at specified image_index', () => {
      const existingMessage = {
        id: 'msg_1',
        content: [
          { type: 'image', url: null },
          { type: 'image', url: null },
        ],
      };
      (store.getMessage as ReturnType<typeof vi.fn>).mockReturnValue(existingMessage);

      const contentPart = { type: 'image', url: 'https://oss/img0.png', width: 1024, height: 1024 };

      handlers.image_partial_update({
        message_id: 'msg_1',
        payload: {
          image_index: 0,
          content_part: contentPart,
          completed_count: 1,
          total_count: 2,
        },
      });

      expect(store.updateMessage).toHaveBeenCalledWith('msg_1', {
        content: expect.arrayContaining([contentPart]),
      });
      // 第二个 slot 仍为原始值
      const updateCall = (store.updateMessage as ReturnType<typeof vi.fn>).mock.calls[0];
      expect(updateCall[1].content[1]).toEqual({ type: 'image', url: null });
    });

    it('should handle error in partial update (failed image)', () => {
      const existingMessage = {
        id: 'msg_1',
        content: [
          { type: 'image', url: 'https://oss/img0.png' },
          { type: 'image', url: null },
        ],
      };
      (store.getMessage as ReturnType<typeof vi.fn>).mockReturnValue(existingMessage);

      handlers.image_partial_update({
        message_id: 'msg_1',
        payload: {
          image_index: 1,
          content_part: null,
          completed_count: 2,
          total_count: 2,
          error: '模型超时',
        },
      });

      const updateCall = (store.updateMessage as ReturnType<typeof vi.fn>).mock.calls[0];
      const content = updateCall[1].content;
      expect(content[0].url).toBe('https://oss/img0.png');
      expect(content[1].failed).toBe(true);
      expect(content[1].error).toBe('模型超时');
    });

    it('should expand content array if image_index exceeds length', () => {
      const existingMessage = {
        id: 'msg_1',
        content: [],
      };
      (store.getMessage as ReturnType<typeof vi.fn>).mockReturnValue(existingMessage);

      const contentPart = { type: 'image', url: 'https://oss/img2.png' };

      handlers.image_partial_update({
        message_id: 'msg_1',
        payload: {
          image_index: 2,
          content_part: contentPart,
          completed_count: 1,
          total_count: 4,
        },
      });

      const updateCall = (store.updateMessage as ReturnType<typeof vi.fn>).mock.calls[0];
      const content = updateCall[1].content;
      expect(content.length).toBeGreaterThanOrEqual(3);
      expect(content[2]).toEqual(contentPart);
    });

    it('should ignore if message not found', () => {
      (store.getMessage as ReturnType<typeof vi.fn>).mockReturnValue(null);

      handlers.image_partial_update({
        message_id: 'nonexistent',
        payload: {
          image_index: 0,
          content_part: { type: 'image', url: 'https://oss/img.png' },
          completed_count: 1,
          total_count: 1,
        },
      });

      expect(store.updateMessage).not.toHaveBeenCalled();
    });

    it('should ignore if message_id is missing', () => {
      handlers.image_partial_update({
        payload: { image_index: 0 },
      });

      expect(store.getMessage).not.toHaveBeenCalled();
      expect(store.updateMessage).not.toHaveBeenCalled();
    });

    it('should ignore if image_index is undefined', () => {
      handlers.image_partial_update({
        message_id: 'msg_1',
        payload: {},
      });

      expect(store.getMessage).not.toHaveBeenCalled();
    });
  });

  // ========================================
  // 11. thinking_chunk handler
  // ========================================

  describe('thinking_chunk', () => {
    it('should call appendStreamingThinking with conversation_id and chunk', () => {
      (store as any).appendStreamingThinking = vi.fn();

      handlers.thinking_chunk({
        conversation_id: 'conv_1',
        chunk: '让我思考一下',
      });

      expect((store as any).appendStreamingThinking).toHaveBeenCalledWith(
        'conv_1',
        '让我思考一下',
      );
    });

    it('should handle chunk from payload.chunk fallback', () => {
      (store as any).appendStreamingThinking = vi.fn();

      handlers.thinking_chunk({
        conversation_id: 'conv_1',
        payload: { chunk: '思考内容' },
      });

      expect((store as any).appendStreamingThinking).toHaveBeenCalledWith(
        'conv_1',
        '思考内容',
      );
    });

    it('should ignore when conversation_id is missing', () => {
      (store as any).appendStreamingThinking = vi.fn();

      handlers.thinking_chunk({
        chunk: '无效',
      });

      expect((store as any).appendStreamingThinking).not.toHaveBeenCalled();
    });

    it('should ignore when chunk is empty', () => {
      (store as any).appendStreamingThinking = vi.fn();

      handlers.thinking_chunk({
        conversation_id: 'conv_1',
      });

      expect((store as any).appendStreamingThinking).not.toHaveBeenCalled();
    });
  });

  // ========================================
  // 12. error handler
  // ========================================

  describe('error', () => {
    it('should not throw on error event', () => {
      expect(() => {
        handlers.error({ message: 'WebSocket error' });
      }).not.toThrow();
    });
  });
});
