import type { KnowledgeReference } from '../../../services/knowledgeReferences';
import { useCallback, useRef, type Dispatch, type SetStateAction } from 'react';
import { toast } from 'react-hot-toast';
import { uploadAudio } from '../../../services/audio';
import type { SkillSelection, SkillTaskMode } from '../../../services/skills';
import { ApiRequestError } from '../../../services/api';
import { createConversation, type ChatSettings } from '../../../services/conversation';
import type { ModelType, UnifiedModel } from '../../../constants/models';
import type { ImageInputInfo, OrderedAttachmentInput } from '../../../services/messageSender';
import type { Message } from '../../../stores/useMessageStore';
import { logger } from '../../../utils/logger';
import type {
  AttachmentDraftTransaction,
  AttachmentSubmissionSnapshot,
} from '../attachments/ChatAttachment.types';

import type { SteerAttachment, SteerReceipt } from './useInputTaskControls';

export interface UseInputSubmissionOptions {
  knowledgeRefs?: KnowledgeReference[];
  takeSelectedSkill?: () => SkillSelection | undefined;
  hasSkillIntent?: boolean;
  skillTaskMode?: SkillTaskMode;
  skillScopeBlocked?: boolean;
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
    extraParams?: Record<string, unknown> | null,
    orderedAttachments?: OrderedAttachmentInput[] | null,
    selectedSkill?: SkillSelection,
  ) => Promise<void>;
  handleImageGeneration: (
    conversationId: string,
    prompt: string,
    images?: string[] | ImageInputInfo[] | null,
    params?: Record<string, unknown> | null,
    selectedSkill?: SkillSelection,
    skillTaskMode?: SkillTaskMode,
  ) => Promise<void>;
  handleVideoGeneration: (
    conversationId: string,
    prompt: string,
    images?: string[] | ImageInputInfo[] | null,
    params?: Record<string, unknown> | null,
    selectedSkill?: SkillSelection,
    skillTaskMode?: SkillTaskMode,
  ) => Promise<void>;
  isEcomMode: boolean;
  effectiveModelType: ModelType;
  smartSubMode: string;
  isStreaming: boolean;
  sendSteer: (message: string, attachments?: SteerAttachment[]) => Promise<SteerReceipt> | null;
  hasPendingSteer?: (message: string, attachments?: SteerAttachment[]) => boolean;
  clearConfirmedSteer: (message: string, conversationId: string | null) => void;
  isUploading: boolean;
  hasImages: boolean;
  hasFiles: boolean;
  attachmentSnapshot: AttachmentSubmissionSnapshot;
  getSendButtonState: (isSubmitting: boolean, isUploading: boolean, hasContent: boolean) => { disabled: boolean };
  detachAttachmentsForSubmission: () => AttachmentDraftTransaction;
}

export function useInputSubmission(options: UseInputSubmissionOptions) {
  const currentConversation = useRef(options.conversationId);
  currentConversation.current = options.conversationId;
  const handleAudioSubmit = useCallback(async (blob: Blob) => {
    if (options.isSubmitting || options.skillScopeBlocked) return;
    options.setIsSubmitting(true);
    const selectedSkill = options.takeSelectedSkill?.();
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
      if (selectedSkill) {
        await options.handleChatMessage('[语音消息]', currentId, [uploaded.audio_url], null, null, null, selectedSkill);
      } else {
        await options.handleChatMessage('[语音消息]', currentId, [uploaded.audio_url]);
      }
    } catch (error) {
      logger.error('inputArea', '发送语音消息失败', error);
      options.setUploadError(error instanceof Error ? error.message : '语音上传失败');
      options.onMessageSent(null);
    } finally {
      options.setIsSubmitting(false);
    }
  }, [options]);

  const handleSubmit = useCallback(async () => {
    if (options.skillScopeBlocked) return;
    if (options.audioBlob) {
      await handleAudioSubmit(options.audioBlob);
      options.clearRecording();
      return;
    }

    const hasAttachments = options.attachmentSnapshot.attachments.length > 0;
    const state = options.getSendButtonState(
      options.isSubmitting,
      options.isUploading,
      !!(options.prompt.trim() || options.hasImages || options.hasFiles || hasAttachments || options.hasSkillIntent),
    );
    if (state.disabled) return;
    const attachments = options.attachmentSnapshot;
    const hasSubmissionImages = attachments.imageInputs.length > 0;
    if (options.smartSubMode === 'image-i2i' && !hasSubmissionImages) {
      toast.error('图生图模式请先上传参考图片');
      return;
    }
    if (options.smartSubMode === 'image-ecom'
      && options.hasImages
      && attachments.imageInputs.length === 0) {
      toast.error('图片还在上传中，请稍候');
      return;
    }
    if (options.effectiveModelType !== 'chat' && attachments.invalidImages.length > 0) {
      toast.error('图片缺少可用原图地址');
      return;
    }
    const maxImages = options.selectedModel.capabilities?.maxImages;
    if (maxImages && attachments.imageInputs.length > maxImages) {
      toast.error(`最多只能上传 ${maxImages} 张图片`);
      return;
    }

    const message = options.prompt.trim();
    const canSteer = !options.knowledgeRefs?.length && !options.hasSkillIntent
      && (!hasAttachments || (options.effectiveModelType === 'chat' && !options.isEcomMode));
    const steerAttachments = hasAttachments ? attachments.orderedAttachments.flatMap(item => {
      const path = item.kind === 'image' ? item.image.workspace_path : item.file.workspace_path;
      return path ? [{ kind: item.kind, workspace_path: path }] : [];
    }) : undefined;
    const steerMessage = message || '请将本条附件用于当前任务。';
    if ((message || hasAttachments) && canSteer
        && (options.isStreaming || options.hasPendingSteer?.(steerMessage, steerAttachments))) {
      if (hasAttachments && (steerAttachments?.length !== attachments.attachments.length
          || steerAttachments.length > 16)) {
        options.setSendError('追加素材需要已上传的工作区文件，每次最多 16 个。');
        return;
      }
      const receipt = steerAttachments
        ? options.sendSteer(steerMessage, steerAttachments) : options.sendSteer(steerMessage);
      if (receipt) {
        const transaction = hasAttachments ? options.detachAttachmentsForSubmission() : null;
        let windowClosed = false;
        options.setIsSubmitting(true);
        try {
          const accepted = await receipt;
          windowClosed = accepted === 'window_closed';
          if (accepted !== true) transaction?.restore();
          if (currentConversation.current !== options.conversationId) return;
          if (accepted === true) {
            options.clearConfirmedSteer(message, options.conversationId);
            options.setSendError(null);
          } else if (!windowClosed) {
            options.setSendError('补充要求的送达状态未确认，输入已保留。可重试发送这条补充。');
          } else {
            options.setSendError(null);
          }
        } finally {
          if (currentConversation.current === options.conversationId) options.setIsSubmitting(false);
        }
        // A scoped server receipt proves this input was never enqueued. Use
        // the normal new-message path; unknown receipts keep the original ID.
        if (!windowClosed) return;
      } else if (hasAttachments) {
        options.setSendError('尚未找到可接收素材的当前任务，输入已保留。');
        return;
      }
    }
    const imageInputs = attachments.imageInputs.length ? attachments.imageInputs : null;
    const fileData = attachments.files.length ? attachments.files : null;
    const selectedSkill = options.takeSelectedSkill?.();

    options.clearPromptForSubmission();
    const attachmentTransaction = options.detachAttachmentsForSubmission();

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
        const params = { generation_type_override: 'image_ecom' };
        if (selectedSkill || options.skillTaskMode) {
          await options.handleImageGeneration(currentId, message, imageInputs, params, selectedSkill, ...(options.skillTaskMode ? [options.skillTaskMode] as const : []));
        } else {
          await options.handleImageGeneration(currentId, message, imageInputs, params);
        }
      } else if (options.effectiveModelType === 'chat') {
        if (options.knowledgeRefs?.length) {
          await options.handleChatMessage(message, currentId, imageInputs, fileData, {knowledge_refs: options.knowledgeRefs}, attachments.orderedAttachments, selectedSkill);
        } else if (selectedSkill) {
          await options.handleChatMessage(message, currentId, imageInputs, fileData,
            null, attachments.orderedAttachments, selectedSkill);
        } else if (attachments.orderedAttachments.length > 0) {
          await options.handleChatMessage(
            message,
            currentId,
            imageInputs,
            fileData,
            null,
            attachments.orderedAttachments,
          );
        } else {
          await options.handleChatMessage(message, currentId, imageInputs, fileData);
        }
      } else if (options.effectiveModelType === 'video') {
        if (selectedSkill || options.skillTaskMode) {
          await options.handleVideoGeneration(currentId, message, imageInputs, null, selectedSkill, ...(options.skillTaskMode ? [options.skillTaskMode] as const : []));
        } else {
          await options.handleVideoGeneration(currentId, message, imageInputs);
        }
      } else {
        if (selectedSkill || options.skillTaskMode) {
          await options.handleImageGeneration(currentId, message, imageInputs, null, selectedSkill, ...(options.skillTaskMode ? [options.skillTaskMode] as const : []));
        } else {
          await options.handleImageGeneration(currentId, message, imageInputs);
        }
      }

    } catch (error) {
      logger.error('inputArea', '发送消息失败', error);
      const disposition = error instanceof ApiRequestError
        ? error.sendDisposition ?? 'rejected'
        : 'rejected';
      if (disposition === 'rejected') {
        options.restorePromptAfterRejection(message);
        attachmentTransaction.restore();
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
