import { renderHook } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { useMediaMessageHandler } from '../useMediaMessageHandler';
import { useTextMessageHandler } from '../useTextMessageHandler';
import type { UnifiedModel } from '../../../constants/models';

const { sendMessageMock } = vi.hoisted(() => ({ sendMessageMock: vi.fn() }));

vi.mock('../../../services/messageSender', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../services/messageSender')>();
  return { ...actual, sendMessage: sendMessageMock };
});

vi.mock('../../../contexts/WebSocketContext', () => ({
  useWebSocketContext: () => ({
    subscribeTaskWithMapping: vi.fn(),
    unsubscribeTask: vi.fn(),
  }),
}));

vi.mock('../../../utils/tabSync', () => ({
  tabSync: { broadcast: vi.fn() },
}));

const selectedModel = { id: 'test-model' } as UnifiedModel;

describe('message handlers error propagation', () => {
  beforeEach(() => {
    sendMessageMock.mockReset();
  });

  it('media handler rethrows a rejected send so the input layer can preserve content', async () => {
    const error = new Error('积分不足');
    sendMessageMock.mockRejectedValueOnce(error);
    const onMessageSent = vi.fn();
    const { result } = renderHook(() => useMediaMessageHandler({
      type: 'image',
      selectedModel,
      onMessagePending: vi.fn(),
      onMessageSent,
    }));

    await expect(result.current.handleMediaGeneration('conv-1', '生成图片')).rejects.toBe(error);
    expect(onMessageSent).not.toHaveBeenCalled();
  });

  it('media handler keeps complete image metadata for persisted user content', async () => {
    sendMessageMock.mockResolvedValueOnce('task-1');
    const onMessagePending = vi.fn();
    const { result } = renderHook(() => useMediaMessageHandler({
      type: 'image',
      selectedModel,
      onMessagePending,
      onMessageSent: vi.fn(),
    }));

    await result.current.handleMediaGeneration('conv-1', '基于这张图生成', [{
      url: 'https://cdn.example.com/reference.png',
      original_url: 'https://cdn.example.com/reference.png',
      thumbnail_url: 'https://cdn.example.com/reference.thumb.webp',
      asset_id: 'asset-1',
      workspace_path: '生成/reference.png',
      name: 'reference.png',
    }]);

    const pendingMessage = onMessagePending.mock.calls[0][0];
    expect(pendingMessage.content[1]).toMatchObject({
      type: 'image',
      url: 'https://cdn.example.com/reference.png',
      original_url: 'https://cdn.example.com/reference.png',
      thumbnail_url: 'https://cdn.example.com/reference.thumb.webp',
      asset_id: 'asset-1',
      workspace_path: '生成/reference.png',
      name: 'reference.png',
    });
    expect(sendMessageMock).toHaveBeenCalledWith(expect.objectContaining({
      content: pendingMessage.content,
    }));
  });

  it('text handler rethrows a rejected send without creating a duplicate error message', async () => {
    const error = new Error('积分不足');
    sendMessageMock.mockRejectedValueOnce(error);
    const onMessageSent = vi.fn();
    const { result } = renderHook(() => useTextMessageHandler({
      selectedModel,
      onMessagePending: vi.fn(),
      onMessageSent,
    }));

    await expect(result.current.handleChatMessage('保留这段输入', 'conv-1')).rejects.toBe(error);
    expect(onMessageSent).not.toHaveBeenCalled();
  });
});
