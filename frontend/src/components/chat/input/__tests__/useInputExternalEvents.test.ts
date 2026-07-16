import { act, renderHook } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { createAttachmentSubmissionSnapshot } from '../../attachments/attachmentSubmission';
import { useInputExternalEvents } from '../useInputExternalEvents';

describe('useInputExternalEvents', () => {
  it('外部电商生成事件使用统一快照中的工作区原图', async () => {
    const handleImageGeneration = vi.fn(async () => undefined);
    const attachmentSnapshot = createAttachmentSubmissionSnapshot([{
      id: 'workspace:product', sourceId: 'product.png', kind: 'image', source: 'workspace',
      status: 'ready', name: 'product.png', previewUrl: 'https://cdn.example.com/thumb.webp',
      originalUrl: 'https://cdn.example.com/product.png', mimeType: 'image/png', size: 10,
    }]);
    renderHook(() => useInputExternalEvents({
      conversationId: 'conversation-1', prompt: '生成主图', attachmentSnapshot,
      handleImageGeneration, handleChatMessage: vi.fn(async () => undefined),
    }));

    await act(async () => window.dispatchEvent(new CustomEvent('ecom:confirm-generate', {
      detail: { images: [{ id: 'task-1' }], conversationId: 'conversation-1' },
    })));

    expect(handleImageGeneration).toHaveBeenCalledWith(
      'conversation-1', '生成主图', ['https://cdn.example.com/product.png'],
      expect.objectContaining({
        product_image_urls: ['https://cdn.example.com/product.png'],
      }),
    );
  });

  it('建议事件使用当前对话发送文本', async () => {
    const handleChatMessage = vi.fn(async () => undefined);
    renderHook(() => useInputExternalEvents({
      conversationId: 'conversation-1', prompt: '',
      attachmentSnapshot: createAttachmentSubmissionSnapshot([]),
      handleImageGeneration: vi.fn(async () => undefined), handleChatMessage,
    }));

    await act(async () => window.dispatchEvent(new CustomEvent('chat:send-suggestion', {
      detail: { text: '继续完善' },
    })));

    expect(handleChatMessage).toHaveBeenCalledWith('继续完善', 'conversation-1');
  });
});
