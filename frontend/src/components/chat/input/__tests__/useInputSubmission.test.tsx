import { act, renderHook } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { UnifiedModel } from '../../../../constants/models';
import { createConversation } from '../../../../services/conversation';
import { ApiRequestError } from '../../../../services/api';
import { uploadAudio } from '../../../../services/audio';
import {
  useInputSubmission,
  type UseInputSubmissionOptions,
} from '../useInputSubmission';
import { createAttachmentSubmissionSnapshot } from '../../attachments/attachmentSubmission';
import type { ChatAttachment } from '../../attachments/ChatAttachment.types';

vi.mock('../../../../services/conversation', () => ({
  createConversation: vi.fn(),
}));
vi.mock('../../../../services/audio', () => ({ uploadAudio: vi.fn() }));
vi.mock('react-hot-toast', () => ({ default: { error: vi.fn() } }));

function image(name: string, originalUrl: string | null): ChatAttachment {
  return {
    id: `workspace:${name}`, sourceId: name, kind: 'image', source: 'workspace',
    status: originalUrl ? 'ready' : 'error', name, mimeType: `image/${name.split('.').pop()}`,
    size: 1024, previewUrl: 'https://cdn.example.com/thumbnail.webp', originalUrl,
    workspacePath: `上传/${name}`,
  };
}

function file(name: string, url: string): ChatAttachment {
  return {
    id: `workspace:${name}`, sourceId: name, kind: 'file', source: 'workspace', status: 'ready',
    name, mimeType: 'application/pdf', size: 4096, url, workspacePath: `上传/${name}`,
  };
}

const snapshot = (attachments: ChatAttachment[]) => createAttachmentSubmissionSnapshot(attachments);

function makeOptions(
  overrides: Partial<UseInputSubmissionOptions> = {},
): UseInputSubmissionOptions {
  return {
    conversationId: 'conversation-1',
    selectedModel: { id: 'model-1', type: 'chat' } as UnifiedModel,
    prompt: '保留这段输入',
    clearPromptForSubmission: vi.fn(),
    restorePromptAfterRejection: vi.fn(),
    audioBlob: null,
    clearRecording: vi.fn(),
    isSubmitting: false,
    setIsSubmitting: vi.fn(),
    setUploadError: vi.fn(),
    setSendError: vi.fn(),
    buildChatSettingsPayload: vi.fn(() => ({})),
    onConversationCreated: vi.fn(),
    onMessageSent: vi.fn(),
    handleChatMessage: vi.fn(async () => undefined),
    handleImageGeneration: vi.fn(async () => undefined),
    handleVideoGeneration: vi.fn(async () => undefined),
    isEcomMode: false,
    effectiveModelType: 'chat',
    smartSubMode: 'chat',
    isStreaming: false,
    sendSteer: vi.fn(),
    isUploading: false,
    hasImages: false,
    hasFiles: false,
    attachmentSnapshot: snapshot([]),
    getSendButtonState: vi.fn(() => ({ disabled: false })),
    detachAttachmentsForSubmission: vi.fn(() => ({ restore: vi.fn() })),
    ...overrides,
  };
}

describe('useInputSubmission', () => {
  beforeEach(() => vi.clearAllMocks());

  it('后端拒绝请求时保留输入和附件', async () => {
    const options = makeOptions({
      handleChatMessage: vi.fn(async () => {
        throw new ApiRequestError(
          'INSUFFICIENT_CREDITS', '积分不足', 402, undefined, 'http', 'rejected',
        );
      }),
    });
    const { result } = renderHook(() => useInputSubmission(options));

    await act(() => result.current.handleSubmit());

    expect(options.clearPromptForSubmission).toHaveBeenCalledOnce();
    expect(options.restorePromptAfterRejection).toHaveBeenCalledWith('保留这段输入');
    expect(options.detachAttachmentsForSubmission).toHaveBeenCalledOnce();
    expect(options.setSendError).toHaveBeenCalledWith('积分不足');
    expect(options.onMessageSent).toHaveBeenCalledWith(null);
  });

  it('请求发出前立即清空输入和附件且接受后不恢复', async () => {
    const options = makeOptions();
    const { result } = renderHook(() => useInputSubmission(options));

    await act(() => result.current.handleSubmit());

    expect(options.clearPromptForSubmission).toHaveBeenCalledOnce();
    expect(options.detachAttachmentsForSubmission).toHaveBeenCalledOnce();
    expect(options.restorePromptAfterRejection).not.toHaveBeenCalled();
    expect(options.setSendError).not.toHaveBeenCalled();
  });

  it('发送结果未知时保持编辑器清空且不恢复附件', async () => {
    const restoreAttachments = vi.fn();
    const options = makeOptions({
      handleChatMessage: vi.fn(async () => {
        throw new ApiRequestError(
          'NETWORK_ERROR', '网络连接中断', undefined, undefined, 'network', 'uncertain',
        );
      }),
      detachAttachmentsForSubmission: vi.fn(() => ({ restore: restoreAttachments })),
    });
    const { result } = renderHook(() => useInputSubmission(options));

    await act(() => result.current.handleSubmit());

    expect(options.clearPromptForSubmission).toHaveBeenCalledOnce();
    expect(options.restorePromptAfterRejection).not.toHaveBeenCalled();
    expect(restoreAttachments).not.toHaveBeenCalled();
  });

  it('新对话创建后使用真实 conversation id 发送', async () => {
    vi.mocked(createConversation).mockResolvedValue({
      id: 'conversation-new',
      title: '新对话',
    });
    const options = makeOptions({ conversationId: null, prompt: '新对话' });
    const { result } = renderHook(() => useInputSubmission(options));

    await act(() => result.current.handleSubmit());

    expect(options.onConversationCreated).toHaveBeenCalledWith('conversation-new', '新对话');
    expect(options.handleChatMessage).toHaveBeenCalledWith(
      '新对话',
      'conversation-new',
      null,
      null,
    );
  });

  it('聊天模式将工作区图片作为图片、普通文件作为文件发送', async () => {
    const options = makeOptions({
      attachmentSnapshot: snapshot([
        { ...image('product.png', 'https://cdn.example.com/workspace/product.png'), size: 2048 },
        file('report.pdf', 'https://cdn.example.com/workspace/report.pdf'),
      ]),
    });
    const { result } = renderHook(() => useInputSubmission(options));

    await act(() => result.current.handleSubmit());

    expect(options.handleChatMessage).toHaveBeenCalledWith(
      '保留这段输入',
      'conversation-1',
      [{
        url: 'https://cdn.example.com/workspace/product.png',
        original_url: 'https://cdn.example.com/workspace/product.png',
        name: 'product.png',
        workspace_path: '上传/product.png',
        mime_type: 'image/png',
        size: 2048,
      }],
      [{
        url: 'https://cdn.example.com/workspace/report.pdf',
        name: 'report.pdf',
        mime_type: 'application/pdf',
        size: 4096,
        workspace_path: '上传/report.pdf',
      }],
    );
  });

  it.each([
    ['image', 'handleImageGeneration'],
    ['video', 'handleVideoGeneration'],
  ] as const)('%s 模式会传递工作区图片原图 URL', async (modelType, handlerName) => {
    const options = makeOptions({
      effectiveModelType: modelType,
      attachmentSnapshot: snapshot([
        image('reference.webp', 'https://cdn.example.com/workspace/reference.webp'),
      ]),
    });
    const { result } = renderHook(() => useInputSubmission(options));

    await act(() => result.current.handleSubmit());

    expect(options[handlerName]).toHaveBeenCalledWith(
      'conversation-1',
      '保留这段输入',
      ['https://cdn.example.com/workspace/reference.webp'],
    );
  });

  it('电商图模式会传递工作区图片原图 URL', async () => {
    const options = makeOptions({
      isEcomMode: true,
      effectiveModelType: 'image',
      smartSubMode: 'image-ecom',
      attachmentSnapshot: snapshot([
        image('product.jpg', 'https://cdn.example.com/workspace/product.jpg'),
      ]),
    });
    const { result } = renderHook(() => useInputSubmission(options));

    await act(() => result.current.handleSubmit());

    expect(options.handleImageGeneration).toHaveBeenCalledWith(
      'conversation-1',
      '保留这段输入',
      ['https://cdn.example.com/workspace/product.jpg'],
      { generation_type_override: 'image_ecom' },
    );
  });

  it('图生图模式接受工作区图片作为参考图', async () => {
    const options = makeOptions({
      effectiveModelType: 'image',
      smartSubMode: 'image-i2i',
      attachmentSnapshot: snapshot([
        image('reference.png', 'https://cdn.example.com/workspace/reference.png'),
      ]),
    });
    const { result } = renderHook(() => useInputSubmission(options));

    await act(() => result.current.handleSubmit());

    expect(options.handleImageGeneration).toHaveBeenCalledWith(
      'conversation-1',
      '保留这段输入',
      ['https://cdn.example.com/workspace/reference.png'],
    );
  });

  it('媒体模式遇到无有效原图的工作区图片时不清空草稿', async () => {
    const options = makeOptions({
      effectiveModelType: 'video',
      attachmentSnapshot: snapshot([image('broken.jpg', null)]),
    });
    const { result } = renderHook(() => useInputSubmission(options));

    await act(() => result.current.handleSubmit());

    expect(options.handleVideoGeneration).not.toHaveBeenCalled();
    expect(options.clearPromptForSubmission).not.toHaveBeenCalled();
    expect(options.detachAttachmentsForSubmission).not.toHaveBeenCalled();
  });

  it('按合并后的本地与工作区图片数校验模型上限', async () => {
    const options = makeOptions({
      selectedModel: {
        id: 'model-1', type: 'chat', capabilities: { maxImages: 1 },
      } as UnifiedModel,
      attachmentSnapshot: snapshot([
        { ...image('upload.png', 'https://cdn.example.com/upload.png'), source: 'upload' },
        image('workspace.png', 'https://cdn.example.com/workspace.png'),
      ]),
    });
    const { result } = renderHook(() => useInputSubmission(options));

    await act(() => result.current.handleSubmit());

    expect(options.handleChatMessage).not.toHaveBeenCalled();
    expect(options.clearPromptForSubmission).not.toHaveBeenCalled();
  });

  it('发送按钮禁用时不启动草稿事务', async () => {
    const options = makeOptions({
      getSendButtonState: vi.fn(() => ({ disabled: true })),
    });
    const { result } = renderHook(() => useInputSubmission(options));

    await act(() => result.current.handleSubmit());

    expect(options.clearPromptForSubmission).not.toHaveBeenCalled();
    expect(options.handleChatMessage).not.toHaveBeenCalled();
  });

  it('图生图模式缺少所有参考图时不清空草稿', async () => {
    const options = makeOptions({ smartSubMode: 'image-i2i', effectiveModelType: 'image' });
    const { result } = renderHook(() => useInputSubmission(options));

    await act(() => result.current.handleSubmit());

    expect(options.clearPromptForSubmission).not.toHaveBeenCalled();
    expect(options.handleImageGeneration).not.toHaveBeenCalled();
  });

  it('电商图模式的本地图片未上传完时不清空草稿', async () => {
    const options = makeOptions({
      smartSubMode: 'image-ecom',
      effectiveModelType: 'image',
      isEcomMode: true,
      hasImages: true,
      attachmentSnapshot: snapshot([{
        ...image('uploading.png', null), source: 'upload', status: 'uploading',
      }]),
    });
    const { result } = renderHook(() => useInputSubmission(options));

    await act(() => result.current.handleSubmit());

    expect(options.clearPromptForSubmission).not.toHaveBeenCalled();
    expect(options.handleImageGeneration).not.toHaveBeenCalled();
  });

  it('有音频草稿时上传并发送语音消息', async () => {
    vi.mocked(uploadAudio).mockResolvedValue({
      audio_url: 'https://cdn.example.com/audio.webm', duration: 3, size: 100,
    });
    const options = makeOptions({ audioBlob: new Blob(['audio'], { type: 'audio/webm' }) });
    const { result } = renderHook(() => useInputSubmission(options));

    await act(() => result.current.handleSubmit());

    expect(options.handleChatMessage).toHaveBeenCalledWith(
      '[语音消息]', 'conversation-1', ['https://cdn.example.com/audio.webm'],
    );
    expect(options.clearRecording).toHaveBeenCalledOnce();
  });
});
