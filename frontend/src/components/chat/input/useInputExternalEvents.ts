import { useEffect } from 'react';
import toast from 'react-hot-toast';
import { useMessageStore } from '../../../stores/useMessageStore';
import { logger } from '../../../utils/logger';
import type { AttachmentSubmissionSnapshot } from '../attachments/ChatAttachment.types';
import type { ImageInputInfo } from '../../../services/messageSender';

interface UseInputExternalEventsOptions {
  conversationId: string | null;
  prompt: string;
  attachmentSnapshot: AttachmentSubmissionSnapshot;
  handleImageGeneration: (
    conversationId: string,
    prompt: string,
    images?: string[] | ImageInputInfo[] | null,
    params?: Record<string, unknown> | null,
  ) => Promise<void>;
  handleChatMessage: (content: string, conversationId: string) => Promise<void>;
}

export function useInputExternalEvents(options: UseInputExternalEventsOptions) {
  const {
    attachmentSnapshot, conversationId, handleChatMessage, handleImageGeneration, prompt,
  } = options;
  useEffect(() => {
    const handler = async (event: Event) => {
      const { images: eventImages, conversationId } = (event as CustomEvent).detail || {};
      if (!eventImages || !conversationId) return;
      try {
        const attachmentImages = attachmentSnapshot.imageInputs;
        const imageUrls = attachmentImages.map((image) => image.url);
        await handleImageGeneration(
          conversationId,
          prompt || '电商主图生成',
          attachmentImages,
          {
            generation_type_override: 'image_ecom',
            image_task_meta: eventImages,
            num_images: eventImages.length,
            product_image_urls: imageUrls,
            style_ref_urls: [],
          },
        );
      } catch (error) {
        logger.error('inputArea', '电商图生成失败', error);
        toast.error('图片生成失败，请重试');
      }
    };
    window.addEventListener('ecom:confirm-generate', handler);
    return () => window.removeEventListener('ecom:confirm-generate', handler);
  }, [attachmentSnapshot, handleImageGeneration, prompt]);

  useEffect(() => {
    const handler = async (event: Event) => {
      const text = (event as CustomEvent<{ text: string }>).detail?.text;
      if (!text || !conversationId) return;
      useMessageStore.getState().clearSuggestions(conversationId);
      window.dispatchEvent(new Event('chat:scroll-to-bottom'));
      try {
        await handleChatMessage(text, conversationId);
      } catch (error) {
        logger.error('inputArea', '发送建议失败', error);
      }
    };
    window.addEventListener('chat:send-suggestion', handler);
    return () => window.removeEventListener('chat:send-suggestion', handler);
  }, [conversationId, handleChatMessage]);
}
