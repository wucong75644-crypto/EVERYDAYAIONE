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

vi.mock('../../../../services/conversation', () => ({
  createConversation: vi.fn(),
}));
vi.mock('../../../../services/audio', () => ({ uploadAudio: vi.fn() }));
vi.mock('react-hot-toast', () => ({ default: { error: vi.fn() } }));

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
    isFileUploading: false,
    hasImages: false,
    hasFiles: false,
    uploadedImageUrls: [],
    uploadedImages: [],
    uploadedFileUrls: [],
    workspaceFiles: [],
    getSendButtonState: vi.fn(() => ({ disabled: false })),
    detachImagesForSubmission: vi.fn(() => vi.fn()),
    detachFilesForSubmission: vi.fn(() => vi.fn()),
    detachWorkspaceFilesForSubmission: vi.fn(() => vi.fn()),
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
    expect(options.detachImagesForSubmission).toHaveBeenCalledOnce();
    expect(options.detachFilesForSubmission).toHaveBeenCalledOnce();
    expect(options.detachWorkspaceFilesForSubmission).toHaveBeenCalledOnce();
    expect(options.setSendError).toHaveBeenCalledWith('积分不足');
    expect(options.onMessageSent).toHaveBeenCalledWith(null);
  });

  it('请求发出前立即清空输入和附件且接受后不恢复', async () => {
    const options = makeOptions();
    const { result } = renderHook(() => useInputSubmission(options));

    await act(() => result.current.handleSubmit());

    expect(options.clearPromptForSubmission).toHaveBeenCalledOnce();
    expect(options.detachImagesForSubmission).toHaveBeenCalledOnce();
    expect(options.detachFilesForSubmission).toHaveBeenCalledOnce();
    expect(options.detachWorkspaceFilesForSubmission).toHaveBeenCalledOnce();
    expect(options.restorePromptAfterRejection).not.toHaveBeenCalled();
    expect(options.setSendError).not.toHaveBeenCalled();
  });

  it('发送结果未知时保持编辑器清空且不恢复附件', async () => {
    const restoreImages = vi.fn();
    const restoreFiles = vi.fn();
    const restoreWorkspaceFiles = vi.fn();
    const options = makeOptions({
      handleChatMessage: vi.fn(async () => {
        throw new ApiRequestError(
          'NETWORK_ERROR', '网络连接中断', undefined, undefined, 'network', 'uncertain',
        );
      }),
      detachImagesForSubmission: vi.fn(() => restoreImages),
      detachFilesForSubmission: vi.fn(() => restoreFiles),
      detachWorkspaceFilesForSubmission: vi.fn(() => restoreWorkspaceFiles),
    });
    const { result } = renderHook(() => useInputSubmission(options));

    await act(() => result.current.handleSubmit());

    expect(options.clearPromptForSubmission).toHaveBeenCalledOnce();
    expect(options.restorePromptAfterRejection).not.toHaveBeenCalled();
    expect(restoreImages).not.toHaveBeenCalled();
    expect(restoreFiles).not.toHaveBeenCalled();
    expect(restoreWorkspaceFiles).not.toHaveBeenCalled();
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
      workspaceFiles: [
        {
          name: 'product.png',
          workspace_path: '上传/product.png',
          cdn_url: 'https://cdn.example.com/workspace/product.png',
          mime_type: 'image/png',
          size: 2048,
        },
        {
          name: 'report.pdf',
          workspace_path: '上传/report.pdf',
          cdn_url: 'https://cdn.example.com/workspace/report.pdf',
          mime_type: 'application/pdf',
          size: 4096,
        },
      ],
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
      workspaceFiles: [{
        name: 'reference.webp',
        workspace_path: '上传/reference.webp',
        cdn_url: 'https://cdn.example.com/workspace/reference.webp',
        mime_type: 'image/webp',
        size: 1024,
      }],
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
      workspaceFiles: [{
        name: 'product.jpg',
        workspace_path: '上传/product.jpg',
        cdn_url: 'https://cdn.example.com/workspace/product.jpg',
        mime_type: 'image/jpeg',
        size: 1024,
      }],
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
      workspaceFiles: [{
        name: 'reference.png',
        workspace_path: '上传/reference.png',
        cdn_url: 'https://cdn.example.com/workspace/reference.png',
        mime_type: 'image/png',
        size: 1024,
      }],
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
      workspaceFiles: [{
        name: 'broken.jpg',
        workspace_path: '上传/broken.jpg',
        cdn_url: 'https://cdn.example.com/workspace-thumbnails/broken.w360.webp',
        mime_type: 'image/jpeg',
        size: 1024,
      }],
    });
    const { result } = renderHook(() => useInputSubmission(options));

    await act(() => result.current.handleSubmit());

    expect(options.handleVideoGeneration).not.toHaveBeenCalled();
    expect(options.clearPromptForSubmission).not.toHaveBeenCalled();
    expect(options.detachWorkspaceFilesForSubmission).not.toHaveBeenCalled();
  });

  it('按合并后的本地与工作区图片数校验模型上限', async () => {
    const options = makeOptions({
      selectedModel: {
        id: 'model-1', type: 'chat', capabilities: { maxImages: 1 },
      } as UnifiedModel,
      uploadedImageUrls: ['https://cdn.example.com/upload.png'],
      uploadedImages: [{ url: 'https://cdn.example.com/upload.png' }],
      workspaceFiles: [{
        name: 'workspace.png', workspace_path: '上传/workspace.png',
        cdn_url: 'https://cdn.example.com/workspace.png', mime_type: 'image/png', size: 10,
      }],
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
      uploadedImageUrls: [],
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
