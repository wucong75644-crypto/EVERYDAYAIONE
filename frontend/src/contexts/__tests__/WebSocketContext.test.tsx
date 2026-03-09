/**
 * WebSocketContext 单元测试
 *
 * 测试覆盖：
 * 1. Provider 初始化和基本功能
 * 2. 消息处理器（8种消息类型）
 * 3. 任务订阅和映射
 * 4. Chunk 缓冲机制（50ms 防抖）
 * 5. 操作上下文回调
 * 6. 任务恢复逻辑
 * 7. 资源清理
 */

import { describe, it, expect, vi, beforeEach, afterEach, type Mock } from 'vitest';
import { renderHook, waitFor, act } from '@testing-library/react';
import { ReactNode } from 'react';
import {
  WebSocketProvider,
  useWebSocketContext,
  type OperationContext,
} from '../WebSocketContext';
import { useWebSocket } from '../../hooks/useWebSocket';
import { useMessageStore } from '../../stores/useMessageStore';
import { useAuthStore } from '../../stores/useAuthStore';
import { useTaskRestorationStore } from '../../stores/useTaskRestorationStore';

// ============================================================
// Mock 配置
// ============================================================

vi.mock('../../hooks/useWebSocket');
vi.mock('../../stores/useAuthStore');
vi.mock('../../stores/useTaskRestorationStore');
vi.mock('../../utils/taskRestoration');
vi.mock('../../stores/useMessageStore', () => ({
  useMessageStore: vi.fn(),
  normalizeMessage: (msg: any) => {
    if (!msg) return undefined;
    return {
      ...msg,
      content: Array.isArray(msg.content) ? msg.content : [],
      status: msg.status || (msg.is_error ? 'failed' : 'completed'),
    };
  },
  getTextContent: vi.fn(),
  getImageUrls: vi.fn(),
  getVideoUrls: vi.fn(),
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
  tabSync: {
    broadcast: vi.fn(),
  },
}));
vi.mock('react-hot-toast', () => ({
  default: {
    success: vi.fn(),
    error: vi.fn(),
  },
}));

// ============================================================
// 测试辅助函数
// ============================================================

/** 创建 WebSocket mock */
function createMockWebSocket() {
  const subscribers = new Map<string, Array<(msg: any) => void>>();

  return {
    isConnected: true,
    isConnecting: false,
    subscribe: vi.fn((type: string, handler: (msg: any) => void) => {
      if (!subscribers.has(type)) {
        subscribers.set(type, []);
      }
      subscribers.get(type)!.push(handler);
      return vi.fn(); // unsubscribe function
    }),
    subscribeTask: vi.fn(),
    unsubscribeTask: vi.fn(),
    // 测试辅助：触发消息
    emit: (type: string, msg: any) => {
      const handlers = subscribers.get(type);
      if (handlers) {
        handlers.forEach((handler) => handler(msg));
      }
    },
    // 测试辅助：清空订阅
    clearSubscribers: () => {
      subscribers.clear();
    },
  };
}

/** 创建 MessageStore mock */
function createMockMessageStore() {
  return {
    setStatus: vi.fn(),
    appendStreamingContent: vi.fn(),
    appendContent: vi.fn(),
    updateTaskProgress: vi.fn(),
    updateMessage: vi.fn(),
    completeTask: vi.fn(),
    failTask: vi.fn(),
    completeStreaming: vi.fn(),
    markConversationCompleted: vi.fn(),
    setIsSending: vi.fn(),
    getMessage: vi.fn(),
    setStreamingContent: vi.fn(),
    addMessage: vi.fn(),
  };
}

/** 创建 Wrapper 组件 */
function createWrapper(mockWs: any, mockMessageStore: any) {
  return ({ children }: { children: ReactNode }) => {
    (useWebSocket as Mock).mockReturnValue(mockWs);
    (useMessageStore as unknown as { getState: Mock }).getState = vi.fn(() => mockMessageStore);

    return <WebSocketProvider>{children}</WebSocketProvider>;
  };
}

// ============================================================
// 测试套件
// ============================================================

describe('WebSocketContext - Provider & Hook', () => {
  let mockWs: ReturnType<typeof createMockWebSocket>;
  let mockMessageStore: ReturnType<typeof createMockMessageStore>;
  let mockAuthStore: any;
  let mockTaskRestorationStore: any;

  beforeEach(() => {
    mockWs = createMockWebSocket();
    mockMessageStore = createMockMessageStore();
    mockAuthStore = {
      user: { id: 'user_123', credits: 100 },
      setUser: vi.fn(),
    };
    mockTaskRestorationStore = {
      hydrateComplete: false,
      placeholdersReady: false,
      setHydrateComplete: vi.fn(),
      setPlaceholdersReady: vi.fn(),
      reset: vi.fn(),
      subscribe: vi.fn(() => vi.fn()),
    };

    (useWebSocket as Mock).mockReturnValue(mockWs);
    (useMessageStore as unknown as { getState: Mock }).getState = vi.fn(() => mockMessageStore);
    (useAuthStore as unknown as { getState: Mock }).getState = vi.fn(() => mockAuthStore);
    (useTaskRestorationStore as unknown as { getState: Mock }).getState = vi.fn(() => mockTaskRestorationStore);
  });

  afterEach(() => {
    vi.clearAllMocks();
    mockWs.clearSubscribers();
  });

  // ========================================
  // 1. 基本功能测试
  // ========================================

  describe('Provider & Hook Basics', () => {
    it('should provide context value', () => {
      const wrapper = createWrapper(mockWs, mockMessageStore);
      const { result } = renderHook(() => useWebSocketContext(), { wrapper });

      expect(result.current).toBeDefined();
      expect(result.current.isConnected).toBe(true);
      expect(result.current.isConnecting).toBe(false);
      expect(typeof result.current.subscribe).toBe('function');
      expect(typeof result.current.subscribeTask).toBe('function');
      expect(typeof result.current.unsubscribeTask).toBe('function');
      expect(typeof result.current.subscribeTaskWithMapping).toBe('function');
      expect(typeof result.current.registerOperation).toBe('function');
    });

    it('should throw error when used outside provider', () => {
      expect(() => {
        renderHook(() => useWebSocketContext());
      }).toThrow('useWebSocketContext must be used within WebSocketProvider');
    });

    it('should subscribe to all message types on mount', () => {
      const wrapper = createWrapper(mockWs, mockMessageStore);
      renderHook(() => useWebSocketContext(), { wrapper });

      expect(mockWs.subscribe).toHaveBeenCalledWith('message_start', expect.any(Function));
      expect(mockWs.subscribe).toHaveBeenCalledWith('message_chunk', expect.any(Function));
      expect(mockWs.subscribe).toHaveBeenCalledWith('message_progress', expect.any(Function));
      expect(mockWs.subscribe).toHaveBeenCalledWith('message_done', expect.any(Function));
      expect(mockWs.subscribe).toHaveBeenCalledWith('message_error', expect.any(Function));
      expect(mockWs.subscribe).toHaveBeenCalledWith('credits_changed', expect.any(Function));
      expect(mockWs.subscribe).toHaveBeenCalledWith('subscribed', expect.any(Function));
      expect(mockWs.subscribe).toHaveBeenCalledWith('error', expect.any(Function));
    });
  });

  // ========================================
  // 2. 消息处理器测试
  // ========================================

  describe('Message Handlers', () => {
    it('should handle message_start', async () => {
      const wrapper = createWrapper(mockWs, mockMessageStore);
      renderHook(() => useWebSocketContext(), { wrapper });

      await act(async () => {
        mockWs.emit('message_start', { message_id: 'msg_123' });
      });

      await waitFor(() => {
        expect(mockMessageStore.setStatus).toHaveBeenCalledWith('msg_123', 'streaming');
      });
    });

    it('should handle message_chunk and buffer it', async () => {
      const wrapper = createWrapper(mockWs, mockMessageStore);
      renderHook(() => useWebSocketContext(), { wrapper });

      // 发送多个 chunk
      await act(async () => {
        mockWs.emit('message_chunk', {
          message_id: 'msg_123',
          conversation_id: 'conv_123',
          chunk: 'Hello ',
        });
        mockWs.emit('message_chunk', {
          message_id: 'msg_123',
          conversation_id: 'conv_123',
          chunk: 'World',
        });
      });

      // 立即检查，应该还没有调用（被缓冲）
      expect(mockMessageStore.appendStreamingContent).not.toHaveBeenCalled();

      // 等待 50ms flush
      await act(async () => {
        await new Promise((resolve) => setTimeout(resolve, 60));
      });

      // 应该批量刷新
      await waitFor(() => {
        expect(mockMessageStore.appendStreamingContent).toHaveBeenCalledWith('conv_123', 'Hello World');
      });
    });

    it('should handle message_chunk with operation context callback', async () => {
      const wrapper = createWrapper(mockWs, mockMessageStore);
      const { result } = renderHook(() => useWebSocketContext(), { wrapper });

      const onStreamChunk = vi.fn();
      const context: OperationContext = {
        type: 'chat',
        operation: 'send',
        conversationId: 'conv_123',
        onStreamChunk,
      };

      act(() => {
        result.current.registerOperation('task_123', context);
      });

      await act(async () => {
        mockWs.emit('message_chunk', {
          message_id: 'msg_123',
          task_id: 'task_123',
          conversation_id: 'conv_123',
          chunk: 'Test',
        });
      });

      // 流式回调应该立即触发
      await waitFor(() => {
        expect(onStreamChunk).toHaveBeenCalledWith('Test', 'Test');
      });
    });

    it('should handle message_progress', async () => {
      const wrapper = createWrapper(mockWs, mockMessageStore);
      renderHook(() => useWebSocketContext(), { wrapper });

      await act(async () => {
        mockWs.emit('message_progress', { task_id: 'task_123', progress: 50 });
      });

      await waitFor(() => {
        expect(mockMessageStore.updateTaskProgress).toHaveBeenCalledWith('task_123', 50);
      });
    });

    it('should handle message_done with task completion', async () => {
      const wrapper = createWrapper(mockWs, mockMessageStore);
      renderHook(() => useWebSocketContext(), { wrapper });

      mockMessageStore.getMessage.mockReturnValue(null); // 消息不存在

      await act(async () => {
        mockWs.emit('message_done', {
          task_id: 'task_123',
          conversation_id: 'conv_123',
          message: {
            id: 'msg_123',
            role: 'assistant',
            content: [{ type: 'text', text: 'Done' }],
            conversation_id: 'conv_123',
            created_at: new Date().toISOString(),
          },
        });
      });

      await waitFor(() => {
        expect(mockMessageStore.updateMessage).toHaveBeenCalledWith(
          'msg_123',
          expect.objectContaining({
            status: 'completed',
          })
        );
        expect(mockMessageStore.completeTask).toHaveBeenCalledWith('task_123');
        expect(mockMessageStore.completeStreaming).toHaveBeenCalledWith('conv_123');
        expect(mockMessageStore.setIsSending).toHaveBeenCalledWith(false);
      });
    });

    it('should skip message_done if message already completed', async () => {
      const wrapper = createWrapper(mockWs, mockMessageStore);
      renderHook(() => useWebSocketContext(), { wrapper });

      // 模拟消息已完成
      mockMessageStore.getMessage.mockReturnValue({
        id: 'msg_123',
        status: 'completed',
      });

      await act(async () => {
        mockWs.emit('message_done', {
          task_id: 'task_123',
          conversation_id: 'conv_123',
          message: {
            id: 'msg_123',
            role: 'assistant',
            content: [{ type: 'text', text: 'Done' }],
            conversation_id: 'conv_123',
            created_at: new Date().toISOString(),
          },
        });
      });

      // 应该跳过更新
      await waitFor(() => {
        expect(mockMessageStore.updateMessage).not.toHaveBeenCalled();
      });
    });

    it('should handle message_done with onComplete callback', async () => {
      const wrapper = createWrapper(mockWs, mockMessageStore);
      const { result } = renderHook(() => useWebSocketContext(), { wrapper });

      const onComplete = vi.fn();
      const context: OperationContext = {
        type: 'chat',
        operation: 'send',
        conversationId: 'conv_123',
        onComplete,
      };

      act(() => {
        result.current.registerOperation('task_123', context);
      });

      mockMessageStore.getMessage.mockReturnValue(null);

      await act(async () => {
        mockWs.emit('message_done', {
          task_id: 'task_123',
          conversation_id: 'conv_123',
          message: {
            id: 'msg_123',
            role: 'assistant',
            content: [{ type: 'text', text: 'Done' }],
            conversation_id: 'conv_123',
            created_at: new Date().toISOString(),
          },
        });
      });

      await waitFor(() => {
        expect(onComplete).toHaveBeenCalledWith(
          expect.objectContaining({
            id: 'msg_123',
            role: 'assistant',
          })
        );
      });
    });

    it('should handle message_error', async () => {
      const wrapper = createWrapper(mockWs, mockMessageStore);
      renderHook(() => useWebSocketContext(), { wrapper });

      await act(async () => {
        mockWs.emit('message_error', {
          task_id: 'task_123',
          message_id: 'msg_123',
          conversation_id: 'conv_123',
          error: {
            code: 'INSUFFICIENT_CREDITS',
            message: '积分不足',
          },
        });
      });

      await waitFor(() => {
        expect(mockMessageStore.updateMessage).toHaveBeenCalledWith(
          'msg_123',
          expect.objectContaining({
            status: 'failed',
            is_error: true,
          })
        );
        expect(mockMessageStore.failTask).toHaveBeenCalledWith('task_123', '积分不足');
        expect(mockMessageStore.completeStreaming).toHaveBeenCalledWith('conv_123');
        expect(mockMessageStore.setIsSending).toHaveBeenCalledWith(false);
      });
    });

    it('should handle message_error with onError callback', async () => {
      const wrapper = createWrapper(mockWs, mockMessageStore);
      const { result } = renderHook(() => useWebSocketContext(), { wrapper });

      const onError = vi.fn();
      const context: OperationContext = {
        type: 'chat',
        operation: 'send',
        conversationId: 'conv_123',
        onError,
      };

      act(() => {
        result.current.registerOperation('task_123', context);
      });

      await act(async () => {
        mockWs.emit('message_error', {
          task_id: 'task_123',
          message_id: 'msg_123',
          error: { message: '测试错误' },
        });
      });

      await waitFor(() => {
        expect(onError).toHaveBeenCalledWith(expect.any(Error));
        expect(onError.mock.calls[0][0].message).toBe('测试错误');
      });
    });

    it('should handle credits_changed', async () => {
      const wrapper = createWrapper(mockWs, mockMessageStore);
      renderHook(() => useWebSocketContext(), { wrapper });

      await act(async () => {
        mockWs.emit('credits_changed', { credits: 200 });
      });

      await waitFor(() => {
        expect(mockAuthStore.setUser).toHaveBeenCalledWith(
          expect.objectContaining({ credits: 200 })
        );
      });
    });

    it('should handle subscribed event with accumulated content', async () => {
      const wrapper = createWrapper(mockWs, mockMessageStore);
      const { result } = renderHook(() => useWebSocketContext(), { wrapper });

      // 先通过 subscribeTaskWithMapping 建立映射
      act(() => {
        result.current.subscribeTaskWithMapping('task_123', 'conv_abc');
      });

      await act(async () => {
        mockWs.emit('subscribed', {
          payload: {
            task_id: 'task_123',
            accumulated: 'Accumulated content',
          },
        });
      });

      await waitFor(() => {
        expect(mockMessageStore.setStreamingContent).toHaveBeenCalledWith('conv_abc', 'Accumulated content');
      });
    });

    it('should handle generic error event', async () => {
      const wrapper = createWrapper(mockWs, mockMessageStore);
      renderHook(() => useWebSocketContext(), { wrapper });

      await act(async () => {
        mockWs.emit('error', { message: 'WebSocket error' });
      });

      // 应该不抛异常，只记录日志
      expect(true).toBe(true);
    });
  });

  // ========================================
  // 3. 任务订阅测试
  // ========================================

  describe('Task Subscription', () => {
    it('should subscribe task with mapping', () => {
      const wrapper = createWrapper(mockWs, mockMessageStore);
      const { result } = renderHook(() => useWebSocketContext(), { wrapper });

      act(() => {
        result.current.subscribeTaskWithMapping('task_123', 'conv_123');
      });

      expect(mockWs.subscribeTask).toHaveBeenCalledWith('task_123');
    });

    it('should not duplicate task subscription', () => {
      const wrapper = createWrapper(mockWs, mockMessageStore);
      const { result } = renderHook(() => useWebSocketContext(), { wrapper });

      act(() => {
        result.current.subscribeTaskWithMapping('task_123', 'conv_123');
        result.current.subscribeTaskWithMapping('task_123', 'conv_123');
      });

      // 应该只订阅一次
      expect(mockWs.subscribeTask).toHaveBeenCalledTimes(1);
    });
  });

  // ========================================
  // 4. 操作上下文测试
  // ========================================

  describe('Operation Context', () => {
    it('should register and retrieve operation context', async () => {
      const wrapper = createWrapper(mockWs, mockMessageStore);
      const { result } = renderHook(() => useWebSocketContext(), { wrapper });

      const context: OperationContext = {
        type: 'chat',
        operation: 'send',
        conversationId: 'conv_123',
      };

      act(() => {
        result.current.registerOperation('task_123', context);
      });

      // 验证：通过触发 message_done 确认上下文存在
      mockMessageStore.getMessage.mockReturnValue(null);

      await act(async () => {
        mockWs.emit('message_done', {
          task_id: 'task_123',
          conversation_id: 'conv_123',
          message: {
            id: 'msg_123',
            role: 'assistant',
            content: [],
            conversation_id: 'conv_123',
            created_at: new Date().toISOString(),
          },
        });
      });

      // 应该能正常完成（不抛异常）
      expect(mockMessageStore.completeTask).toHaveBeenCalled();
    });
  });

  // ========================================
  // 5. 资源清理测试
  // ========================================

  describe('Cleanup', () => {
    it('should unsubscribe all handlers on unmount', () => {
      const unsubscribe = vi.fn();
      mockWs.subscribe.mockReturnValue(unsubscribe);

      const wrapper = createWrapper(mockWs, mockMessageStore);
      const { unmount } = renderHook(() => useWebSocketContext(), { wrapper });

      unmount();

      // 11 个消息类型的订阅都应该被取消（含 image_partial_update, memory_extracted, agent_step）
      expect(unsubscribe).toHaveBeenCalledTimes(11);
    });

    it('should clear flush timer on unmount', async () => {
      const wrapper = createWrapper(mockWs, mockMessageStore);
      const { unmount } = renderHook(() => useWebSocketContext(), { wrapper });

      // 触发 chunk（启动定时器）
      await act(async () => {
        mockWs.emit('message_chunk', {
          message_id: 'msg_123',
          conversation_id: 'conv_123',
          chunk: 'Test',
        });
      });

      // 立即卸载（定时器应该被清理）
      unmount();

      // 等待超过 50ms
      await act(async () => {
        await new Promise((resolve) => setTimeout(resolve, 60));
      });

      // 不应该触发 flush（因为已清理）
      expect(mockMessageStore.appendStreamingContent).not.toHaveBeenCalled();
    });

    it('should clear chunk buffer on message_error', async () => {
      const wrapper = createWrapper(mockWs, mockMessageStore);
      renderHook(() => useWebSocketContext(), { wrapper });

      // 发送 chunk
      await act(async () => {
        mockWs.emit('message_chunk', {
          message_id: 'msg_123',
          conversation_id: 'conv_123',
          chunk: 'Test',
        });
      });

      // 立即发送错误
      await act(async () => {
        mockWs.emit('message_error', {
          message_id: 'msg_123',
          task_id: 'task_123',
          error: { message: 'Error' },
        });
      });

      // 等待超过 50ms
      await act(async () => {
        await new Promise((resolve) => setTimeout(resolve, 60));
      });

      // chunk 应该被丢弃，不触发 flush
      expect(mockMessageStore.appendStreamingContent).not.toHaveBeenCalled();
    });
  });

  // ========================================
  // 7. 边界情况测试
  // ========================================

  describe('Edge Cases', () => {
    it('should handle message_chunk without conversation_id (fallback path)', async () => {
      const wrapper = createWrapper(mockWs, mockMessageStore);
      renderHook(() => useWebSocketContext(), { wrapper });

      await act(async () => {
        mockWs.emit('message_chunk', {
          message_id: 'msg_123',
          chunk: 'Test',
          // 缺少 conversation_id
        });
      });

      // 不应该抛异常，也不应该缓冲
      expect(true).toBe(true);
    });

    it('should handle message_done without task_id', async () => {
      const wrapper = createWrapper(mockWs, mockMessageStore);
      renderHook(() => useWebSocketContext(), { wrapper });

      await act(async () => {
        mockWs.emit('message_done', {
          message_id: 'msg_123',
          message: {
            id: 'msg_123',
            role: 'assistant',
            content: [{ type: 'text', text: 'Done' }],
          },
          // 缺少 task_id
        });
      });

      await waitFor(() => {
        expect(mockMessageStore.updateMessage).toHaveBeenCalled();
      });
    });

    it('should handle message_done with only message_id', async () => {
      const wrapper = createWrapper(mockWs, mockMessageStore);
      renderHook(() => useWebSocketContext(), { wrapper });

      await act(async () => {
        mockWs.emit('message_done', {
          task_id: 'task_123',
          message_id: 'msg_123',
          // 缺少 message 和 conversation_id
        });
      });

      await waitFor(() => {
        expect(mockMessageStore.setStatus).toHaveBeenCalledWith('msg_123', 'completed');
        expect(mockMessageStore.completeTask).toHaveBeenCalledWith('task_123');
      });
    });

    it('should handle credits_changed without current user', async () => {
      mockAuthStore.user = null; // 无用户

      const wrapper = createWrapper(mockWs, mockMessageStore);
      renderHook(() => useWebSocketContext(), { wrapper });

      await act(async () => {
        mockWs.emit('credits_changed', { credits: 200 });
      });

      // 不应该抛异常
      expect(mockAuthStore.setUser).not.toHaveBeenCalled();
    });

    it('should handle subscribed without task mapping', async () => {
      const wrapper = createWrapper(mockWs, mockMessageStore);
      renderHook(() => useWebSocketContext(), { wrapper });

      // 不建立映射，直接触发 subscribed
      await act(async () => {
        mockWs.emit('subscribed', {
          payload: {
            task_id: 'unknown_task',
            accumulated: 'Content',
          },
        });
      });

      // 没有映射，不应调用 setStreamingContent
      expect(mockMessageStore.setStreamingContent).not.toHaveBeenCalled();
    });
  });
});
