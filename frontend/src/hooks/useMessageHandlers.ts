/**
 * 消息处理 Hook（组合器）
 *
 * 组合聊天、图像生成、视频生成的处理逻辑
 * 各策略逻辑已提取到独立 Handler 文件
 */

import { type UnifiedModel } from '../constants/models';
import { type Message } from '../services/message';
import {
  type AspectRatio,
  type ImageResolution,
  type ImageOutputFormat,
} from '../services/image';
import {
  type VideoFrames,
  type VideoAspectRatio,
} from '../services/video';
import { useTextMessageHandler } from './handlers/useTextMessageHandler';
import { useImageMessageHandler } from './handlers/useImageMessageHandler';
import { useVideoMessageHandler } from './handlers/useVideoMessageHandler';

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
  conversationTitle: string;
  onMessagePending: (message: Message) => void;
  onMessageSent: (aiMessage?: Message | null) => void;
  onStreamContent?: (text: string, conversationId: string) => void;
  onStreamStart?: (conversationId: string, model: string) => void;
  onMediaTaskSubmitted?: () => void;
}

export function useMessageHandlers(params: UseMessageHandlersParams) {
  const {
    selectedModel,
    aspectRatio,
    resolution,
    outputFormat,
    videoFrames,
    videoAspectRatio,
    removeWatermark,
    thinkingEffort,
    deepThinkMode,
    conversationTitle,
    onMessagePending,
    onMessageSent,
    onStreamContent,
    onStreamStart,
    onMediaTaskSubmitted,
  } = params;

  // 文本消息处理
  const { handleChatMessage } = useTextMessageHandler({
    selectedModel,
    thinkingEffort,
    deepThinkMode,
    onMessagePending,
    onMessageSent,
    onStreamContent,
    onStreamStart,
  });

  // 图片消息处理
  const { handleImageGeneration } = useImageMessageHandler({
    selectedModel,
    aspectRatio,
    resolution,
    outputFormat,
    conversationTitle,
    onMessagePending,
    onMessageSent,
    onMediaTaskSubmitted,
  });

  // 视频消息处理
  const { handleVideoGeneration } = useVideoMessageHandler({
    selectedModel,
    videoFrames,
    videoAspectRatio,
    removeWatermark,
    conversationTitle,
    onMessagePending,
    onMessageSent,
    onMediaTaskSubmitted,
  });

  return {
    handleChatMessage,
    handleImageGeneration,
    handleVideoGeneration,
  };
}
