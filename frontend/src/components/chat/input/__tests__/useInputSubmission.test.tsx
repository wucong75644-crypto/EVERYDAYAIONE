import { act, renderHook } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { UnifiedModel } from '../../../../constants/models';
import { createConversation } from '../../../../services/conversation';
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
    setPrompt: vi.fn(),
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
    handleRemoveAllImages: vi.fn(),
    handleRemoveAllFiles: vi.fn(),
    onWorkspaceFilesConsumed: vi.fn(),
    ...overrides,
  };
}

describe('useInputSubmission', () => {
  beforeEach(() => vi.clearAllMocks());

  it('后端拒绝请求时保留输入和附件', async () => {
    const options = makeOptions({
      handleChatMessage: vi.fn(async () => {
        throw new Error('积分不足');
      }),
    });
    const { result } = renderHook(() => useInputSubmission(options));

    await act(() => result.current.handleSubmit());

    expect(options.setPrompt).not.toHaveBeenCalled();
    expect(options.handleRemoveAllImages).not.toHaveBeenCalled();
    expect(options.handleRemoveAllFiles).not.toHaveBeenCalled();
    expect(options.onWorkspaceFilesConsumed).not.toHaveBeenCalled();
    expect(options.setSendError).toHaveBeenCalledWith('积分不足');
    expect(options.onMessageSent).toHaveBeenCalledWith(null);
  });

  it('后端接受请求后才清空输入和附件', async () => {
    const options = makeOptions();
    const { result } = renderHook(() => useInputSubmission(options));

    await act(() => result.current.handleSubmit());

    expect(options.setPrompt).toHaveBeenCalledWith('');
    expect(options.handleRemoveAllImages).toHaveBeenCalledOnce();
    expect(options.handleRemoveAllFiles).toHaveBeenCalledOnce();
    expect(options.onWorkspaceFilesConsumed).toHaveBeenCalledOnce();
    expect(options.setSendError).not.toHaveBeenCalled();
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
