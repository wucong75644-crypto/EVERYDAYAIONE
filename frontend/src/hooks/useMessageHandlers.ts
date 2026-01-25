/**
 * 消息处理 Hook
 *
 * 提取聊天、图像生成、视频生成的处理逻辑
 */

import { type UnifiedModel } from '../constants/models';
import { sendMessageStream, createMessage, type Message } from '../services/message';
import {
  generateImage,
  editImage,
  pollTaskUntilDone,
  type ImageModel,
  type AspectRatio,
  type ImageResolution,
  type ImageOutputFormat,
} from '../services/image';
import {
  generateTextToVideo,
  generateImageToVideo,
  pollVideoTaskUntilDone,
  type VideoModel,
  type VideoFrames,
  type VideoAspectRatio,
} from '../services/video';

interface UseMessageHandlersParams {
  selectedModel: UnifiedModel;
  aspectRatio: AspectRatio;
  resolution: ImageResolution;
  outputFormat: ImageOutputFormat;
  videoFrames: VideoFrames;
  videoAspectRatio: VideoAspectRatio;
  removeWatermark: boolean;
  thinkingEffort?: 'minimal' | 'low' | 'medium' | 'high';
  deepThinkMode?: boolean;
  onMessagePending: (message: Message) => void;
  onMessageSent: (aiMessage?: Message | null) => void;
  onStreamContent?: (text: string) => void;
}

export function useMessageHandlers({
  selectedModel,
  aspectRatio,
  resolution,
  outputFormat,
  videoFrames,
  videoAspectRatio,
  removeWatermark,
  thinkingEffort,
  deepThinkMode,
  onMessagePending,
  onMessageSent,
  onStreamContent,
}: UseMessageHandlersParams) {
  /**
   * 处理聊天消息
   */
  const handleChatMessage = async (
    messageContent: string,
    currentConversationId: string,
    imageUrl: string | null = null
  ) => {
    // 1. 乐观更新：立即显示用户消息（无需等待数据库）
    const optimisticUserMessage: Message = {
      id: `temp-${Date.now()}`,
      conversation_id: currentConversationId,
      role: 'user',
      content: messageContent,
      image_url: imageUrl,
      video_url: null,
      credits_cost: 0,
      created_at: new Date().toISOString(),
    };
    onMessagePending(optimisticUserMessage);

    try {
      // 2. 发送流式请求（后端会自动保存用户消息）
      await sendMessageStream(
        currentConversationId,
        {
          content: messageContent,
          model_id: selectedModel.id,
          image_url: imageUrl, // 发送图片URL给AI（VQA）
          thinking_effort: thinkingEffort, // 推理强度（Gemini 3）
          thinking_mode: deepThinkMode ? 'deep_think' : 'default', // Deep Think 模式（Gemini 3 Pro）
        },
        {
          onUserMessage: (userMessage: Message) => {
            // 后端返回真实的用户消息（已保存到数据库）
            // 替换临时的乐观更新消息，确保使用数据库中的真实 ID
            onMessagePending(userMessage);
          },
          onStart: (model: string) => {
            // AI 开始生成
            console.log('AI 开始生成:', model);
          },
          onContent: (text: string) => {
            // 流式内容
            if (onStreamContent) onStreamContent(text);
          },
          onDone: (assistantMessage: Message | null, _credits: number) => {
            // 3. 生成完成：显示 AI 回复消息
            if (assistantMessage) {
              onMessageSent(assistantMessage);
            } else {
              onMessageSent(null);
            }
          },
          onError: (error: string) => {
            console.error('流式响应错误:', error);
            const errorMessage: Message = {
              id: `error-${Date.now()}`,
              conversation_id: currentConversationId,
              role: 'assistant',
              content: `AI 响应错误: ${error}`,
              image_url: null,
              video_url: null,
              is_error: true,
              credits_cost: 0,
              created_at: new Date().toISOString(),
            };
            onMessageSent(errorMessage);
          },
        }
      );
    } catch (error) {
      console.error('发送消息失败:', error);
      const errorMessage: Message = {
        id: `error-${Date.now()}`,
        conversation_id: currentConversationId,
        role: 'assistant',
        content: `发送失败: ${error instanceof Error ? error.message : '未知错误'}`,
        image_url: null,
        video_url: null,
        is_error: true,
        credits_cost: 0,
        created_at: new Date().toISOString(),
      };
      onMessageSent(errorMessage);
    }
  };

  /**
   * 处理图像生成
   */
  const handleImageGeneration = async (
    messageContent: string,
    currentConversationId: string,
    imageUrl: string | null = null
  ) => {
    try {
      let response;

      // 图像编辑模式（用户上传图片）- 只要有图片就使用编辑API
      if (imageUrl) {
        // 1. 乐观更新：立即显示用户消息（无需等待数据库）
        const optimisticUserMessage: Message = {
          id: `temp-${Date.now()}`,
          conversation_id: currentConversationId,
          role: 'user',
          content: messageContent,
          image_url: imageUrl,
          video_url: null,
          credits_cost: 0,
          created_at: new Date().toISOString(),
        };
        onMessagePending(optimisticUserMessage);

        // 2. 后台保存用户消息到数据库，并替换临时消息
        const realUserMessage = await createMessage(currentConversationId, {
          content: messageContent,
          role: 'user',
          image_url: imageUrl,
        });
        onMessagePending(realUserMessage);

        // 3. 调用图像编辑 API
        response = await editImage({
          prompt: messageContent,
          image_urls: [imageUrl],
          size: aspectRatio,
          output_format: outputFormat,
          wait_for_result: false,
        });
      } else {
        // 生成模式：乐观更新
        // 1. 立即显示用户消息（无需等待数据库）
        const optimisticUserMessage: Message = {
          id: `temp-${Date.now()}`,
          conversation_id: currentConversationId,
          role: 'user',
          content: messageContent,
          image_url: imageUrl,
          video_url: null,
          credits_cost: 0,
          created_at: new Date().toISOString(),
        };
        onMessagePending(optimisticUserMessage);

        // 2. 后台保存用户消息到数据库，并替换临时消息
        const realUserMessage = await createMessage(currentConversationId, {
          content: messageContent,
          role: 'user',
          image_url: imageUrl,
        });
        onMessagePending(realUserMessage);

        // 3. 调用图像生成 API
        response = await generateImage({
          prompt: messageContent,
          model: selectedModel.id as ImageModel,
          size: aspectRatio,
          output_format: outputFormat,
          resolution: selectedModel.supportsResolution ? resolution : undefined,
          wait_for_result: false,
        });
      }

      // 如果还在处理中，开始轮询
      if (response.status === 'pending' || response.status === 'processing') {
        const result = await pollTaskUntilDone(response.task_id, {
          interval: 2000,
          maxWait: 300000,
        });

        if (result.status === 'success' && result.image_urls.length > 0) {
          // 保存 AI 回复消息到数据库（包含生成的图片）
          const savedAiMessage = await createMessage(currentConversationId, {
            content: selectedModel.capabilities.imageEditing ? '图片编辑完成' : '图片已生成完成',
            role: 'assistant',
            image_url: result.image_urls[0],
            credits_cost: response.credits_consumed,
          });
          onMessageSent(savedAiMessage);
        } else {
          throw new Error(result.fail_msg || '图片处理失败');
        }
      } else if (response.status === 'success' && response.image_urls.length > 0) {
        // 直接返回结果：保存 AI 回复消息到数据库
        const savedAiMessage = await createMessage(currentConversationId, {
          content: selectedModel.capabilities.imageEditing ? '图片编辑完成' : '图片已生成完成',
          role: 'assistant',
          image_url: response.image_urls[0],
          credits_cost: response.credits_consumed,
        });
        onMessageSent(savedAiMessage);
      } else {
        throw new Error('图片处理失败');
      }
    } catch (error) {
      console.error('图片处理失败:', error);
      const errorMessage: Message = {
        id: `error-${Date.now()}`,
        conversation_id: currentConversationId,
        role: 'assistant',
        content: `图片处理失败: ${error instanceof Error ? error.message : '未知错误'}`,
        image_url: null,
        video_url: null,
        is_error: true,
        credits_cost: 0,
        created_at: new Date().toISOString(),
      };
      onMessageSent(errorMessage);
    }
  };

  /**
   * 处理视频生成
   */
  const handleVideoGeneration = async (
    messageContent: string,
    currentConversationId: string,
    imageUrl: string | null = null
  ) => {
    try {
      let response;

      // 图生视频模式（用户上传图片）
      if (imageUrl && selectedModel.capabilities.imageToVideo) {
        // 1. 乐观更新：立即显示用户消息
        const optimisticUserMessage: Message = {
          id: `temp-${Date.now()}`,
          conversation_id: currentConversationId,
          role: 'user',
          content: messageContent,
          image_url: imageUrl,
          video_url: null,
          credits_cost: 0,
          created_at: new Date().toISOString(),
        };
        onMessagePending(optimisticUserMessage);

        // 2. 后台保存用户消息到数据库，并替换临时消息
        const realUserMessage = await createMessage(currentConversationId, {
          content: messageContent,
          role: 'user',
          image_url: imageUrl,
        });
        onMessagePending(realUserMessage);

        // 3. 调用图生视频 API
        response = await generateImageToVideo({
          prompt: messageContent,
          image_url: imageUrl,
          model: selectedModel.id as VideoModel,
          n_frames: videoFrames,
          aspect_ratio: videoAspectRatio,
          remove_watermark: removeWatermark,
          wait_for_result: false,
        });
      } else {
        // 文生视频模式
        // 1. 立即显示用户消息
        const optimisticUserMessage: Message = {
          id: `temp-${Date.now()}`,
          conversation_id: currentConversationId,
          role: 'user',
          content: messageContent,
          image_url: null,
          video_url: null,
          credits_cost: 0,
          created_at: new Date().toISOString(),
        };
        onMessagePending(optimisticUserMessage);

        // 2. 后台保存用户消息到数据库，并替换临时消息
        const realUserMessage = await createMessage(currentConversationId, {
          content: messageContent,
          role: 'user',
        });
        onMessagePending(realUserMessage);

        // 3. 调用文生视频 API
        response = await generateTextToVideo({
          prompt: messageContent,
          model: selectedModel.id as VideoModel,
          n_frames: videoFrames,
          aspect_ratio: videoAspectRatio,
          remove_watermark: removeWatermark,
          wait_for_result: false,
        });
      }

      // 开始轮询（视频生成通常较慢）
      if (response.status === 'pending' || response.status === 'processing') {
        const result = await pollVideoTaskUntilDone(response.task_id, {
          interval: 5000, // 5秒轮询一次
          maxWait: 600000, // 最多等待10分钟
        });

        if (result.status === 'success' && result.video_url) {
          // 保存 AI 回复消息到数据库（包含生成的视频）
          const savedAiMessage = await createMessage(currentConversationId, {
            content: selectedModel.capabilities.imageToVideo ? '视频生成完成（图生视频）' : '视频生成完成',
            role: 'assistant',
            video_url: result.video_url,
            credits_cost: response.credits_consumed,
          });
          onMessageSent(savedAiMessage);
        } else {
          throw new Error(result.fail_msg || '视频生成失败');
        }
      } else if (response.status === 'success' && response.video_url) {
        // 直接返回结果（不太可能，视频生成通常是异步的）
        const savedAiMessage = await createMessage(currentConversationId, {
          content: selectedModel.capabilities.imageToVideo ? '视频生成完成（图生视频）' : '视频生成完成',
          role: 'assistant',
          video_url: response.video_url,
          credits_cost: response.credits_consumed,
        });
        onMessageSent(savedAiMessage);
      } else {
        throw new Error('视频生成失败');
      }
    } catch (error) {
      console.error('视频生成失败:', error);
      const errorMessage: Message = {
        id: `error-${Date.now()}`,
        conversation_id: currentConversationId,
        role: 'assistant',
        content: `视频生成失败: ${error instanceof Error ? error.message : '未知错误'}`,
        image_url: null,
        video_url: null,
        is_error: true,
        credits_cost: 0,
        created_at: new Date().toISOString(),
      };
      onMessageSent(errorMessage);
    }
  };

  return {
    handleChatMessage,
    handleImageGeneration,
    handleVideoGeneration,
  };
}
