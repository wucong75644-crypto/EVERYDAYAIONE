/**
 * useRegenerateHandlers Hook 单元测试
 *
 * 测试重新生成/重试的逻辑判断
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useRegenerateHandlers } from '../useRegenerateHandlers';
import type { Message, ContentPart } from '../../stores/useMessageStore';

// ============================================================
// Mocks
// ============================================================

// Mock sendMessage
const mockSendMessage = vi.fn();
vi.mock('../../services/messageSender', async () => {
  const actual = await vi.importActual('../../services/messageSender');
  return {
    ...actual,
    sendMessage: (...args: unknown[]) => mockSendMessage(...args),
  };
});

// Mock useWebSocketContext
const mockSubscribeTaskWithMapping = vi.fn();
vi.mock('../../contexts/WebSocketContext', () => ({
  useWebSocketContext: () => ({
    subscribeTaskWithMapping: mockSubscribeTaskWithMapping,
  }),
}));

// Mock toast
vi.mock('react-hot-toast', () => ({
  default: {
    error: vi.fn(),
  },
}));

// ============================================================
// 辅助函数
// ============================================================

function toContent(text: string): ContentPart[] {
  return [{ type: 'text', text }];
}

function createTestMessage(overrides: Partial<Message> = {}): Message {
  return {
    id: 'msg-1',
    conversation_id: 'conv-1',
    role: 'assistant',
    content: toContent('test response'),
    status: 'completed',
    created_at: new Date().toISOString(),
    ...overrides,
  };
}

function createUserMessage(overrides: Partial<Message> = {}): Message {
  return {
    id: 'user-msg-1',
    conversation_id: 'conv-1',
    role: 'user',
    content: toContent('user question'),
    status: 'completed',
    created_at: new Date().toISOString(),
    ...overrides,
  };
}

// ============================================================
// 测试
// ============================================================

describe('useRegenerateHandlers', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockSendMessage.mockResolvedValue('task-123');
  });

  afterEach(() => {
    vi.resetAllMocks();
  });

  it('should not call sendMessage when conversationId is null', async () => {
    const { result } = renderHook(() =>
      useRegenerateHandlers({
        conversationId: null,
        setMessages: vi.fn(),
      })
    );

    const targetMessage = createTestMessage();
    const userMessage = createUserMessage();

    await act(async () => {
      await result.current.handleRegenerate(targetMessage, userMessage);
    });

    expect(mockSendMessage).not.toHaveBeenCalled();
  });

  it('should call sendMessage with "retry" operation for error messages', async () => {
    const { result } = renderHook(() =>
      useRegenerateHandlers({
        conversationId: 'conv-1',
        setMessages: vi.fn(),
      })
    );

    const targetMessage = createTestMessage({
      id: 'failed-msg',
      is_error: true,
      status: 'failed',
    });
    const userMessage = createUserMessage({
      content: toContent('原始问题'),
    });

    await act(async () => {
      await result.current.handleRegenerate(targetMessage, userMessage);
    });

    expect(mockSendMessage).toHaveBeenCalledWith(
      expect.objectContaining({
        operation: 'retry',
        originalMessageId: 'failed-msg',
        conversationId: 'conv-1',
      })
    );
  });

  it('should call sendMessage with "regenerate" operation for successful messages', async () => {
    const { result } = renderHook(() =>
      useRegenerateHandlers({
        conversationId: 'conv-1',
        setMessages: vi.fn(),
      })
    );

    const targetMessage = createTestMessage({
      id: 'success-msg',
      is_error: false,
      status: 'completed',
    });
    const userMessage = createUserMessage();

    await act(async () => {
      await result.current.handleRegenerate(targetMessage, userMessage);
    });

    expect(mockSendMessage).toHaveBeenCalledWith(
      expect.objectContaining({
        operation: 'regenerate',
        originalMessageId: 'success-msg',
      })
    );
  });

  it('should pass user message content to sendMessage', async () => {
    const { result } = renderHook(() =>
      useRegenerateHandlers({
        conversationId: 'conv-1',
        setMessages: vi.fn(),
      })
    );

    const userContent = toContent('用户的原始问题');
    const targetMessage = createTestMessage();
    const userMessage = createUserMessage({ content: userContent });

    await act(async () => {
      await result.current.handleRegenerate(targetMessage, userMessage);
    });

    expect(mockSendMessage).toHaveBeenCalledWith(
      expect.objectContaining({
        content: userContent,
      })
    );
  });

  it('should extract and pass model from generation_params', async () => {
    const { result } = renderHook(() =>
      useRegenerateHandlers({
        conversationId: 'conv-1',
        setMessages: vi.fn(),
      })
    );

    const targetMessage = createTestMessage({
      generation_params: {
        model: 'gemini-3-pro',
        type: 'chat',
      },
    });
    const userMessage = createUserMessage();

    await act(async () => {
      await result.current.handleRegenerate(targetMessage, userMessage);
    });

    expect(mockSendMessage).toHaveBeenCalledWith(
      expect.objectContaining({
        model: 'gemini-3-pro',
      })
    );
  });

  it('should detect image type from generation_params', async () => {
    const { result } = renderHook(() =>
      useRegenerateHandlers({
        conversationId: 'conv-1',
        setMessages: vi.fn(),
      })
    );

    const targetMessage = createTestMessage({
      generation_params: {
        type: 'image',
        model: 'google/nano-banana',
        aspect_ratio: '16:9',
      },
    });
    const userMessage = createUserMessage();

    await act(async () => {
      await result.current.handleRegenerate(targetMessage, userMessage);
    });

    expect(mockSendMessage).toHaveBeenCalledWith(
      expect.objectContaining({
        generationType: 'image',
        params: expect.objectContaining({
          aspect_ratio: '16:9',
        }),
      })
    );
  });

  it('should detect video type from content', async () => {
    const { result } = renderHook(() =>
      useRegenerateHandlers({
        conversationId: 'conv-1',
        setMessages: vi.fn(),
      })
    );

    const targetMessage = createTestMessage({
      content: [
        { type: 'video', url: 'https://example.com/video.mp4' },
      ],
    });
    const userMessage = createUserMessage();

    await act(async () => {
      await result.current.handleRegenerate(targetMessage, userMessage);
    });

    expect(mockSendMessage).toHaveBeenCalledWith(
      expect.objectContaining({
        generationType: 'video',
      })
    );
  });

  it('should pass subscribeTask function', async () => {
    const { result } = renderHook(() =>
      useRegenerateHandlers({
        conversationId: 'conv-1',
        setMessages: vi.fn(),
      })
    );

    const targetMessage = createTestMessage();
    const userMessage = createUserMessage();

    await act(async () => {
      await result.current.handleRegenerate(targetMessage, userMessage);
    });

    expect(mockSendMessage).toHaveBeenCalledWith(
      expect.objectContaining({
        subscribeTask: mockSubscribeTaskWithMapping,
      })
    );
  });

  it('should extract thinking params for chat regeneration', async () => {
    const { result } = renderHook(() =>
      useRegenerateHandlers({
        conversationId: 'conv-1',
        setMessages: vi.fn(),
      })
    );

    const targetMessage = createTestMessage({
      generation_params: {
        type: 'chat',
        thinking_effort: 'high',
        thinking_mode: 'deep_think',
      },
    });
    const userMessage = createUserMessage();

    await act(async () => {
      await result.current.handleRegenerate(targetMessage, userMessage);
    });

    expect(mockSendMessage).toHaveBeenCalledWith(
      expect.objectContaining({
        params: {
          thinking_effort: 'high',
          thinking_mode: 'deep_think',
        },
      })
    );
  });

  it('should handle sendMessage errors gracefully', async () => {
    const toast = await import('react-hot-toast');
    mockSendMessage.mockRejectedValue(new Error('网络错误'));

    const { result } = renderHook(() =>
      useRegenerateHandlers({
        conversationId: 'conv-1',
        setMessages: vi.fn(),
      })
    );

    const targetMessage = createTestMessage();
    const userMessage = createUserMessage();

    await act(async () => {
      await result.current.handleRegenerate(targetMessage, userMessage);
    });

    expect(toast.default.error).toHaveBeenCalledWith('网络错误');
  });
});
