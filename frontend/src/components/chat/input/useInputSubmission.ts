import { useCallback, type Dispatch, type SetStateAction } from 'react';
import toast from 'react-hot-toast';
import { uploadAudio } from '../../../services/audio';
import { ApiRequestError } from '../../../services/api';
import { createConversation, type ChatSettings } from '../../../services/conversation';
import type { ModelType, UnifiedModel } from '../../../constants/models';
import type { ImageInputInfo } from '../../../services/messageSender';
import type { Message } from '../../../stores/useMessageStore';
import { toOriginalImageUrl } from '../../../utils/imageUrlRules';
import { logger } from '../../../utils/logger';

interface WorkspaceFile {
  name: string;
  workspace_path: string;
  cdn_url: string | null;
  mime_type: string | null;
  size: number;
}

export interface UseInputSubmissionOptions {
  conversationId: string | null;
  selectedModel: UnifiedModel;
  prompt: string;
  clearPromptForSubmission: () => void;
  restorePromptAfterRejection: (submittedPrompt: string) => void;
  audioBlob: Blob | null;
  clearRecording: () => void;
  isSubmitting: boolean;
  setIsSubmitting: Dispatch<SetStateAction<boolean>>;
  setUploadError: Dispatch<SetStateAction<string | null>>;
  setSendError: Dispatch<SetStateAction<string | null>>;
  buildChatSettingsPayload: () => ChatSettings;
  onConversationCreated: (id: string, title: string) => void;
  onMessageSent: (message?: Message | null) => void;
  handleChatMessage: (
    content: string,
    conversationId: string,
    images?: string[] | ImageInputInfo[] | null,
    files?: Array<{ url: string; name: string; mime_type: string; size: number; workspace_path?: string }> | null,
  ) => Promise<void>;
  handleImageGeneration: (
    conversationId: string,
    prompt: string,
    imageUrls?: string[] | null,
    params?: Record<string, unknown> | null,
  ) => Promise<void>;
  handleVideoGeneration: (
    conversationId: string,
    prompt: string,
    imageUrls?: string[] | null,
  ) => Promise<void>;
  isEcomMode: boolean;
  effectiveModelType: ModelType;
  smartSubMode: string;
  isStreaming: boolean;
  sendSteer: (message: string) => void;
  isUploading: boolean;
  isFileUploading: boolean;
  hasImages: boolean;
  hasFiles: boolean;
  uploadedImageUrls: string[];
  uploadedImages: ImageInputInfo[];
  uploadedFileUrls: Array<{
    url: string;
    name: string;
    mime_type: string;
    size: number;
    workspace_path?: string;
  }>;
  workspaceFiles: WorkspaceFile[];
  getSendButtonState: (isSubmitting: boolean, isUploading: boolean, hasContent: boolean) => { disabled: boolean };
  detachImagesForSubmission: () => () => void;
  detachFilesForSubmission: () => () => void;
  detachWorkspaceFilesForSubmission: () => () => void;
}

export function useInputSubmission(options: UseInputSubmissionOptions) {
  const handleAudioSubmit = useCallback(async (blob: Blob) => {
    if (options.isSubmitting) return;
    options.setIsSubmitting(true);
    try {
      let currentId = options.conversationId;
      if (!currentId) {
        const conversation = await createConversation({
          title: '语音对话',
          model_id: options.selectedModel.id,
          chat_settings: options.buildChatSettingsPayload(),
        });
        currentId = conversation.id;
        options.onConversationCreated(currentId, '语音对话');
      }
      const uploaded = await uploadAudio(blob);
      await options.handleChatMessage('[语音消息]', currentId, [uploaded.audio_url]);
    } catch (error) {
      logger.error('inputArea', '发送语音消息失败', error);
      options.setUploadError(error instanceof Error ? error.message : '语音上传失败');
      options.onMessageSent(null);
    } finally {
      options.setIsSubmitting(false);
    }
  }, [options]);

  const handleSubmit = useCallback(async () => {
    if (options.audioBlob) {
      await handleAudioSubmit(options.audioBlob);
      options.clearRecording();
      return;
    }

    const hasWorkspaceFiles = options.workspaceFiles.length > 0;
    const state = options.getSendButtonState(
      options.isSubmitting,
      options.isUploading || options.isFileUploading,
      !!(options.prompt.trim() || options.hasImages || options.hasFiles || hasWorkspaceFiles),
    );
    if (state.disabled) return;
    if (options.smartSubMode === 'image-i2i' && !options.hasImages) {
      toast.error('图生图模式请先上传参考图片');
      return;
    }
    if (options.smartSubMode === 'image-ecom'
      && options.hasImages
      && options.uploadedImageUrls.length === 0) {
      toast.error('图片还在上传中，请稍候');
      return;
    }

    const message = options.prompt.trim();
    if (options.isStreaming && message) options.sendSteer(message);
    const imageUrls = options.uploadedImageUrls.length
      ? [...options.uploadedImageUrls]
      : null;
    const imageInputs = options.uploadedImages.length
      ? [...options.uploadedImages]
      : null;
    const workspaceFiles = options.workspaceFiles.map(file => ({
      url: toOriginalImageUrl(file.cdn_url),
      name: file.name,
      mime_type: file.mime_type || 'application/octet-stream',
      size: file.size,
      workspace_path: file.workspace_path,
    }));
    const mergedFiles = [...options.uploadedFileUrls, ...workspaceFiles];
    const fileData = mergedFiles.length ? mergedFiles : null;

    options.clearPromptForSubmission();
    const restoreImages = options.detachImagesForSubmission();
    const restoreFiles = options.detachFilesForSubmission();
    const restoreWorkspaceFiles = options.detachWorkspaceFilesForSubmission();

    options.setIsSubmitting(true);
    window.dispatchEvent(new Event('chat:scroll-to-bottom'));
    try {
      const title = message.slice(0, 20) || '新对话';
      let currentId = options.conversationId;
      if (!currentId) {
        const conversation = await createConversation({
          title,
          model_id: options.selectedModel.id,
          chat_settings: options.buildChatSettingsPayload(),
        });
        currentId = conversation.id;
        options.onConversationCreated(currentId, title);
      }

      if (options.isEcomMode) {
        await options.handleImageGeneration(currentId, message, imageUrls, {
          generation_type_override: 'image_ecom',
        });
      } else if (options.effectiveModelType === 'chat') {
        await options.handleChatMessage(message, currentId, imageInputs ?? imageUrls, fileData);
      } else if (options.effectiveModelType === 'video') {
        await options.handleVideoGeneration(currentId, message, imageUrls);
      } else {
        await options.handleImageGeneration(currentId, message, imageUrls);
      }

    } catch (error) {
      logger.error('inputArea', '发送消息失败', error);
      const disposition = error instanceof ApiRequestError
        ? error.sendDisposition ?? 'rejected'
        : 'rejected';
      if (disposition === 'rejected') {
        options.restorePromptAfterRejection(message);
        restoreImages();
        restoreFiles();
        restoreWorkspaceFiles();
      }
      const fallbackMessage = disposition === 'uncertain'
        ? '发送状态确认中，请勿重复发送'
        : '发送失败，请重试';
      options.setSendError(error instanceof Error ? error.message : fallbackMessage);
      options.onMessageSent(null);
    } finally {
      options.setIsSubmitting(false);
    }
  }, [handleAudioSubmit, options]);

  return { handleAudioSubmit, handleSubmit };
}
