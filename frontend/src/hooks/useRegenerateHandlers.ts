/**
 * 重新生成处理器 Hook
 *
 * 封装所有消息重新生成逻辑（聊天、图片、视频），避免 MessageArea 代码膨胀
 */

import { useCallback, useRef } from 'react';
import {
  createMessage,
  sendMessageStream,
  regenerateMessageStream,
  type Message,
} from '../services/message';
import { generateImage, editImage, queryTaskStatus as getImageTaskStatus, type ImageModel } from '../services/image';
import { generateTextToVideo, generateImageToVideo, queryVideoTaskStatus as getVideoTaskStatus, type VideoModel } from '../services/video';
import { useTaskStore } from '../stores/useTaskStore';
import { useAuthStore } from '../stores/useAuthStore';
import toast from 'react-hot-toast';
import type { Message as CacheMessage, MessageCacheEntry } from '../stores/useChatStore';
import { createTempMessagePair } from '../utils/messageFactory';
import { type UnifiedModel, ALL_MODELS } from '../constants/models';

/** 根据模型 ID 获取模型类型 */
function getModelTypeById(modelId: string): 'chat' | 'image' | 'video' | null {
  return ALL_MODELS.find((m) => m.id === modelId)?.type ?? null;
}

// 默认参数
const DEFAULTS = {
  IMAGE_MODEL: 'google/nano-banana',
  VIDEO_MODEL: 'sora-2-text-to-video',
  I2V_MODEL: 'sora-2-image-to-video',
  ASPECT_RATIO: '1:1' as const,
  VIDEO_FRAMES: '10' as const,
  VIDEO_ASPECT_RATIO: 'landscape' as const,
};

interface RegenerateHandlersOptions {
  conversationId: string | null;
  conversationTitle: string;
  setMessages: React.Dispatch<React.SetStateAction<Message[]>>;
  scrollToBottom: (smooth?: boolean) => void;
  onMessageUpdate?: (newLastMessage: string) => void;
  resetRegeneratingState: () => void;
  setRegeneratingId: (id: string | null) => void;
  setIsRegeneratingAI: (value: boolean) => void;
  modelId?: string | null;
  selectedModel?: UnifiedModel | null;
  userScrolledAway: boolean;
  getCachedMessages: (conversationId: string) => MessageCacheEntry | null;
  updateCachedMessages: (conversationId: string, messages: CacheMessage[], hasMore?: boolean) => void;
  toStoreMessage: (msg: Message) => CacheMessage;
  onMediaTaskSubmitted?: () => void;
}

/** 媒体重新生成配置 */
interface MediaRegenConfig {
  type: 'image' | 'video';
  placeholderText: string;
  successContent: string;
  pollInterval: number;
  /** 用户消息时间戳（用于保持消息顺序） */
  userMessageTimestamp: string;
  pollFn: (taskId: string) => Promise<{ status: string; fail_msg?: string | null }>;
  extractUrl: (result: unknown) => { image_url?: string; video_url?: string };
}

/** 保存用户消息到数据库 */
async function saveUserMessage(
  conversationId: string,
  userMessage: Message,
  tempUserId: string,
  setMessages: React.Dispatch<React.SetStateAction<Message[]>>,
  createdAt: string
): Promise<Message> {
  const realUserMessage = await createMessage(conversationId, {
    content: userMessage.content,
    role: 'user',
    image_url: userMessage.image_url,
    created_at: createdAt,
  });
  setMessages((prev) =>
    prev.map((m) => (m.id === tempUserId ? { ...realUserMessage, created_at: m.created_at } : m))
  );
  return realUserMessage;
}

/** 处理重新生成错误 */
async function handleRegenError(
  error: unknown,
  conversationId: string,
  placeholderId: string,
  mediaType: 'image' | 'video',
  setMessages: React.Dispatch<React.SetStateAction<Message[]>>,
  createdAt: string
): Promise<void> {
  const errorText = mediaType === 'image' ? '图片生成失败' : '视频生成失败';
  const errorMsg = `${errorText}: ${error instanceof Error ? error.message : '未知错误'}`;

  try {
    const savedError = await createMessage(conversationId, {
      content: errorMsg,
      role: 'assistant',
      is_error: true,
      created_at: createdAt,
    });
    setMessages((prev) => prev.map((m) => (m.id === placeholderId ? savedError : m)));
  } catch {
    setMessages((prev) =>
      prev.map((m) => (m.id === placeholderId ? { ...m, content: errorMsg, is_error: true } : m))
    );
  }
  toast.error(errorMsg);
}

export function useRegenerateHandlers({
  conversationId,
  conversationTitle,
  setMessages,
  scrollToBottom,
  onMessageUpdate,
  resetRegeneratingState,
  setRegeneratingId,
  setIsRegeneratingAI,
  modelId,
  selectedModel,
  userScrolledAway,
  getCachedMessages,
  updateCachedMessages,
  toStoreMessage,
  onMediaTaskSubmitted,
}: RegenerateHandlersOptions) {
  const regeneratingContentRef = useRef<string>('');

  /** 通用媒体轮询处理 */
  const handleMediaPolling = useCallback(
    (
      taskId: string,
      placeholderId: string,
      creditsConsumed: number,
      config: MediaRegenConfig
    ) => {
      const { startMediaTask, startPolling, completeMediaTask, failMediaTask } = useTaskStore.getState();
      const { refreshUser } = useAuthStore.getState();

      startMediaTask({
        taskId,
        conversationId: conversationId!,
        conversationTitle,
        type: config.type,
        placeholderId,
      });

      resetRegeneratingState();
      if (onMediaTaskSubmitted) onMediaTaskSubmitted();

      startPolling(
        taskId,
        async () => {
          const result = await config.pollFn(taskId);
          if (result.status === 'success') return { done: true, result };
          if (result.status === 'failed') {
            return { done: true, error: new Error(result.fail_msg || `${config.type}生成失败`) };
          }
          return { done: false };
        },
        {
          onSuccess: async (result: unknown) => {
            const mediaUrl = config.extractUrl(result);
            const aiCreatedAt = new Date(new Date(config.userMessageTimestamp).getTime() + 1).toISOString();
            try {
              const savedMsg = await createMessage(conversationId!, {
                content: config.successContent,
                role: 'assistant',
                image_url: mediaUrl.image_url,
                video_url: mediaUrl.video_url,
                credits_cost: creditsConsumed,
                created_at: aiCreatedAt,
              });
              setMessages((prev) => prev.map((m) => (m.id === placeholderId ? savedMsg : m)));
              completeMediaTask(taskId);
              refreshUser();
              if (onMessageUpdate) onMessageUpdate(savedMsg.content);
            } catch (err) {
              console.error(`保存${config.type}消息失败:`, err);
              failMediaTask(taskId);
            }
          },
          onError: async (error: Error) => {
            const errorCreatedAt = new Date(new Date(config.userMessageTimestamp).getTime() + 1).toISOString();
            await handleRegenError(error, conversationId!, placeholderId, config.type, setMessages, errorCreatedAt);
            failMediaTask(taskId);
          },
        },
        config.pollInterval
      );
    },
    [conversationId, conversationTitle, setMessages, onMessageUpdate, resetRegeneratingState, onMediaTaskSubmitted]
  );

  /** 策略 A：失败消息原地重新生成 */
  const regenerateFailedMessage = useCallback(
    async (messageId: string, targetMessage: Message) => {
      if (!conversationId) return;

      const regenConvId = conversationId;
      setRegeneratingId(messageId);
      setIsRegeneratingAI(true);
      const contentRef = { current: '' };

      setMessages((prev) =>
        prev.map((m) => (m.id === messageId ? { ...m, content: '', is_error: false } : m))
      );

      try {
        await regenerateMessageStream(conversationId, messageId, {
          onContent: (content: string) => {
            contentRef.current += content;
            setMessages((prev) => {
              if (conversationId !== regenConvId) return prev;
              return prev.map((m) =>
                m.id === messageId ? { ...m, content: contentRef.current, is_error: false } : m
              );
            });
            if (!userScrolledAway) scrollToBottom();
          },
          onDone: (finalMessage: Message | null) => {
            if (!finalMessage) return;
            setMessages((prev) => {
              if (conversationId !== regenConvId) return prev;
              const updated = prev.map((m) => (m.id === messageId ? finalMessage : m));
              queueMicrotask(() => {
                const cached = getCachedMessages(conversationId);
                if (cached) updateCachedMessages(conversationId, updated.map(toStoreMessage), cached.hasMore);
              });
              return updated;
            });
            resetRegeneratingState();
            if (onMessageUpdate) onMessageUpdate(finalMessage.content);
          },
          onError: (error: string) => {
            setMessages((prev) => prev.map((m) => (m.id === messageId ? targetMessage : m)));
            resetRegeneratingState();
            toast.error(`重试失败: ${error}`);
          },
        });
      } catch {
        setMessages((prev) => prev.map((m) => (m.id === messageId ? targetMessage : m)));
        resetRegeneratingState();
        toast.error('重新生成失败，请重试');
      }
    },
    [conversationId, userScrolledAway, scrollToBottom, getCachedMessages, updateCachedMessages, toStoreMessage, onMessageUpdate, resetRegeneratingState, setMessages, setRegeneratingId, setIsRegeneratingAI]
  );

  /** 策略 B：成功消息新增对话 */
  const regenerateAsNewMessage = useCallback(
    async (userMessage: Message) => {
      if (!conversationId) return;

      const { tempUserMessage, tempAiMessage, tempUserId, newStreamingId } = createTempMessagePair(
        conversationId, userMessage, ''
      );

      setRegeneratingId(newStreamingId);
      setIsRegeneratingAI(true);
      regeneratingContentRef.current = '';
      setMessages((prev) => [...prev, tempUserMessage, tempAiMessage]);
      scrollToBottom();

      const chatModelId = modelId || selectedModel?.id || 'gemini-3-flash';

      await sendMessageStream(
        conversationId,
        { content: userMessage.content, model_id: chatModelId },
        {
          onUserMessage: (realUser: Message) => {
            setMessages((prev) => prev.map((m) => (m.id === tempUserId ? realUser : m)));
          },
          onContent: (content: string) => {
            regeneratingContentRef.current += content;
            setMessages((prev) =>
              prev.map((m) => (m.id === newStreamingId ? { ...m, content: regeneratingContentRef.current } : m))
            );
            if (!userScrolledAway) scrollToBottom();
          },
          onDone: (finalMessage: Message | null) => {
            if (finalMessage) {
              setMessages((prev) => {
                const updated = prev.map((m) => (m.id === newStreamingId ? finalMessage : m));
                queueMicrotask(() => {
                  const cached = getCachedMessages(conversationId!);
                  if (cached) updateCachedMessages(conversationId!, updated.map(toStoreMessage), cached.hasMore);
                });
                return updated;
              });
              if (onMessageUpdate) onMessageUpdate(finalMessage.content);
            }
            resetRegeneratingState();
          },
          onError: (error: string) => {
            setMessages((prev) => prev.filter((m) => m.id !== tempUserId && m.id !== newStreamingId));
            resetRegeneratingState();
            toast.error(`重新生成失败: ${error}`);
          },
        }
      );
    },
    [conversationId, modelId, selectedModel, userScrolledAway, scrollToBottom, getCachedMessages, updateCachedMessages, toStoreMessage, onMessageUpdate, resetRegeneratingState, setMessages, setRegeneratingId, setIsRegeneratingAI]
  );

  /** 策略 C：图片消息重新生成 */
  const regenerateImageMessage = useCallback(
    async (userMessage: Message) => {
      if (!conversationId) return;

      const { tempUserMessage, tempAiMessage, tempUserId, newStreamingId } = createTempMessagePair(
        conversationId, userMessage, '图片生成中...'
      );

      setRegeneratingId(newStreamingId);
      setIsRegeneratingAI(true);
      setMessages((prev) => [...prev, tempUserMessage, tempAiMessage]);
      scrollToBottom();

      try {
        // 使用 tempUserMessage 的时间戳保存用户消息，确保消息顺序正确
        const userTimestamp = tempUserMessage.created_at;
        await saveUserMessage(conversationId, userMessage, tempUserId, setMessages, userTimestamp);

        const savedImageModel = modelId && getModelTypeById(modelId) === 'image' ? modelId : null;
        const currentImageModel = selectedModel?.type === 'image' ? selectedModel.id : null;
        const imageModelId = savedImageModel || currentImageModel || DEFAULTS.IMAGE_MODEL;

        const response = userMessage.image_url
          ? await editImage({ prompt: userMessage.content, image_urls: [userMessage.image_url], size: DEFAULTS.ASPECT_RATIO, output_format: 'png', wait_for_result: false })
          : await generateImage({ prompt: userMessage.content, model: imageModelId as ImageModel, size: DEFAULTS.ASPECT_RATIO, output_format: 'png', wait_for_result: false });

        const successContent = userMessage.image_url ? '图片编辑完成' : '图片已生成完成';

        if (response.status === 'pending' || response.status === 'processing') {
          handleMediaPolling(response.task_id, newStreamingId, response.credits_consumed, {
            type: 'image',
            placeholderText: '正在生成图片...',
            successContent,
            pollInterval: 2000,
            userMessageTimestamp: userTimestamp,
            pollFn: getImageTaskStatus,
            extractUrl: (r) => ({ image_url: (r as { image_urls: string[] }).image_urls[0] }),
          });
        } else if (response.status === 'success' && response.image_urls.length > 0) {
          const aiCreatedAt = new Date(new Date(userTimestamp).getTime() + 1).toISOString();
          const savedMsg = await createMessage(conversationId, { content: successContent, role: 'assistant', image_url: response.image_urls[0], credits_cost: response.credits_consumed, created_at: aiCreatedAt });
          setMessages((prev) => prev.map((m) => (m.id === newStreamingId ? savedMsg : m)));
          resetRegeneratingState();
          if (onMediaTaskSubmitted) onMediaTaskSubmitted();
          if (onMessageUpdate) onMessageUpdate(savedMsg.content);
        } else {
          throw new Error('图片生成失败');
        }
      } catch (error) {
        const errorCreatedAt = new Date(new Date(tempUserMessage.created_at).getTime() + 1).toISOString();
        await handleRegenError(error, conversationId, newStreamingId, 'image', setMessages, errorCreatedAt);
        resetRegeneratingState();
        if (onMediaTaskSubmitted) onMediaTaskSubmitted();
      }
    },
    [conversationId, modelId, selectedModel, setMessages, scrollToBottom, onMessageUpdate, resetRegeneratingState, setRegeneratingId, setIsRegeneratingAI, onMediaTaskSubmitted, handleMediaPolling]
  );

  /** 策略 D：视频消息重新生成 */
  const regenerateVideoMessage = useCallback(
    async (userMessage: Message) => {
      if (!conversationId) return;

      const { tempUserMessage, tempAiMessage, tempUserId, newStreamingId } = createTempMessagePair(
        conversationId, userMessage, '视频生成中...'
      );

      setRegeneratingId(newStreamingId);
      setIsRegeneratingAI(true);
      setMessages((prev) => [...prev, tempUserMessage, tempAiMessage]);
      scrollToBottom();

      try {
        // 使用 tempUserMessage 的时间戳保存用户消息，确保消息顺序正确
        const userTimestamp = tempUserMessage.created_at;
        await saveUserMessage(conversationId, userMessage, tempUserId, setMessages, userTimestamp);

        const savedVideoModel = modelId && getModelTypeById(modelId) === 'video' ? modelId : null;
        const currentVideoModel = selectedModel?.type === 'video' ? selectedModel.id : null;
        const videoModelId = savedVideoModel || currentVideoModel || DEFAULTS.VIDEO_MODEL;

        // 图生视频模型选择
        const savedModel = savedVideoModel ? ALL_MODELS.find((m) => m.id === savedVideoModel) : null;
        const savedSupportsI2V = savedModel?.type === 'video' && savedModel.capabilities.imageToVideo;
        const currentSupportsI2V = selectedModel?.type === 'video' && selectedModel.capabilities.imageToVideo;
        const i2vModelId = (savedSupportsI2V ? savedVideoModel : null) || (currentSupportsI2V ? selectedModel!.id : null) || DEFAULTS.I2V_MODEL;

        const response = userMessage.image_url
          ? await generateImageToVideo({ prompt: userMessage.content, image_url: userMessage.image_url, model: i2vModelId as VideoModel, n_frames: DEFAULTS.VIDEO_FRAMES, aspect_ratio: DEFAULTS.VIDEO_ASPECT_RATIO, remove_watermark: false, wait_for_result: false })
          : await generateTextToVideo({ prompt: userMessage.content, model: videoModelId as VideoModel, n_frames: DEFAULTS.VIDEO_FRAMES, aspect_ratio: DEFAULTS.VIDEO_ASPECT_RATIO, remove_watermark: false, wait_for_result: false });

        const successContent = userMessage.image_url ? '视频生成完成（图生视频）' : '视频生成完成';

        if (response.status === 'pending' || response.status === 'processing') {
          handleMediaPolling(response.task_id, newStreamingId, response.credits_consumed, {
            type: 'video',
            placeholderText: '正在生成视频...',
            successContent,
            pollInterval: 5000,
            userMessageTimestamp: userTimestamp,
            pollFn: getVideoTaskStatus,
            extractUrl: (r) => ({ video_url: (r as { video_url: string }).video_url }),
          });
        } else if (response.status === 'success' && response.video_url) {
          const aiCreatedAt = new Date(new Date(userTimestamp).getTime() + 1).toISOString();
          const savedMsg = await createMessage(conversationId, { content: successContent, role: 'assistant', video_url: response.video_url, credits_cost: response.credits_consumed, created_at: aiCreatedAt });
          setMessages((prev) => prev.map((m) => (m.id === newStreamingId ? savedMsg : m)));
          resetRegeneratingState();
          if (onMediaTaskSubmitted) onMediaTaskSubmitted();
          if (onMessageUpdate) onMessageUpdate(savedMsg.content);
        } else {
          throw new Error('视频生成失败');
        }
      } catch (error) {
        const errorCreatedAt = new Date(new Date(tempUserMessage.created_at).getTime() + 1).toISOString();
        await handleRegenError(error, conversationId, newStreamingId, 'video', setMessages, errorCreatedAt);
        resetRegeneratingState();
        if (onMediaTaskSubmitted) onMediaTaskSubmitted();
      }
    },
    [conversationId, modelId, selectedModel, setMessages, scrollToBottom, onMessageUpdate, resetRegeneratingState, setRegeneratingId, setIsRegeneratingAI, onMediaTaskSubmitted, handleMediaPolling]
  );

  return {
    regenerateFailedMessage,
    regenerateAsNewMessage,
    regenerateImageMessage,
    regenerateVideoMessage,
  };
}
