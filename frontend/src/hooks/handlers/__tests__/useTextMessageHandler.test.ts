/**
 * useTextMessageHandler 单元测试
 * 测试文本消息处理逻辑
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';
import { useTextMessageHandler } from '../useTextMessageHandler';
import * as messageService from '../../../services/message';
import { mockMessage, mockChatModel } from '../../../test/testUtils';

// Mock message service
vi.mock('../../../services/message', () => ({
  sendMessageStream: vi.fn(),
}));

describe('useTextMessageHandler', () => {
  const mockOnMessagePending = vi.fn();
  const mockOnMessageSent = vi.fn();
  const mockOnStreamContent = vi.fn();
  const mockOnStreamStart = vi.fn();

  const defaultProps = {
    selectedModel: mockChatModel,
    onMessagePending: mockOnMessagePending,
    onMessageSent: mockOnMessageSent,
    onStreamContent: mockOnStreamContent,
    onStreamStart: mockOnStreamStart,
  };

  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('应该成功发送文本消息', async () => {
    const { result } = renderHook(() => useTextMessageHandler(defaultProps));

    const conversationId = 'conv-1';
    const messageContent = 'Hello, AI!';

    // Mock 成功的流式响应
    vi.mocked(messageService.sendMessageStream).mockImplementation(
      async (_, __, callbacks) => {
        callbacks.onUserMessage?.(mockMessage);
        callbacks.onContent?.('Hello');
        callbacks.onContent?.(' back!');
        callbacks.onDone?.({ ...mockMessage, role: 'assistant', content: 'Hello back!' }, 1);
      }
    );

    await result.current.handleChatMessage(messageContent, conversationId);

    // 验证调用
    await waitFor(() => {
      expect(mockOnMessagePending).toHaveBeenCalled();
      expect(mockOnStreamStart).toHaveBeenCalledWith(conversationId, mockChatModel.id);
      expect(mockOnStreamContent).toHaveBeenCalledWith('Hello', conversationId);
      expect(mockOnStreamContent).toHaveBeenCalledWith(' back!', conversationId);
      expect(mockOnMessageSent).toHaveBeenCalled();
    });
  });

  it('应该处理带图片的文本消息', async () => {
    const { result } = renderHook(() => useTextMessageHandler(defaultProps));

    const conversationId = 'conv-1';
    const messageContent = 'Describe this image';
    const imageUrl = 'https://example.com/image.jpg';

    vi.mocked(messageService.sendMessageStream).mockImplementation(
      async (_, request, callbacks) => {
        expect(request.image_url).toBe(imageUrl);
        callbacks.onUserMessage?.(mockMessage);
        callbacks.onDone?.({ ...mockMessage, role: 'assistant', content: 'Response' }, 1);
      }
    );

    await result.current.handleChatMessage(messageContent, conversationId, imageUrl);

    await waitFor(() => {
      expect(messageService.sendMessageStream).toHaveBeenCalledWith(
        conversationId,
        expect.objectContaining({
          content: messageContent,
          image_url: imageUrl,
        }),
        expect.any(Object)
      );
    });
  });

  it('应该处理错误情况', async () => {
    const { result } = renderHook(() => useTextMessageHandler(defaultProps));

    const conversationId = 'conv-1';
    const messageContent = 'Test error';

    vi.mocked(messageService.sendMessageStream).mockImplementation(
      async (_, __, callbacks) => {
        callbacks.onError?.('API Error');
      }
    );

    await result.current.handleChatMessage(messageContent, conversationId);

    await waitFor(() => {
      expect(mockOnMessageSent).toHaveBeenCalledWith(
        expect.objectContaining({
          is_error: true,
        })
      );
    });
  });

  it('应该传递 thinking 参数', async () => {
    const propsWithThinking = {
      ...defaultProps,
      thinkingEffort: 'high' as const,
      deepThinkMode: true,
    };

    const { result } = renderHook(() => useTextMessageHandler(propsWithThinking));

    vi.mocked(messageService.sendMessageStream).mockImplementation(
      async (_, request) => {
        expect(request.thinking_effort).toBe('high');
        expect(request.thinking_mode).toBe('deep_think');
      }
    );

    await result.current.handleChatMessage('Test', 'conv-1');

    await waitFor(() => {
      expect(messageService.sendMessageStream).toHaveBeenCalled();
    });
  });

  it('应该在 catch 块中处理异常', async () => {
    const { result } = renderHook(() => useTextMessageHandler(defaultProps));

    vi.mocked(messageService.sendMessageStream).mockRejectedValue(
      new Error('Network error')
    );

    await result.current.handleChatMessage('Test', 'conv-1');

    await waitFor(() => {
      expect(mockOnMessageSent).toHaveBeenCalledWith(
        expect.objectContaining({
          is_error: true,
        })
      );
    });
  });
});
