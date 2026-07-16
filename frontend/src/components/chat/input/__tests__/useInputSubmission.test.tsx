import { act, renderHook } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { UnifiedModel } from '../../../../constants/models';
import { createConversation } from '../../../../services/conversation';
import { ApiRequestError } from '../../../../services/api';
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
});
