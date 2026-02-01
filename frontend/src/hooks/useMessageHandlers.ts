/**
 * 消息处理 Hook（组合器）
 *
 * 组合聊天、图像生成、视频生成的处理逻辑
 * 使用统一的 useMediaMessageHandler 处理图片/视频
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
import { useMediaMessageHandler } from './handlers/useMediaMessageHandler';

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

  // 图片消息处理（使用统一媒体处理器）
  const { handleMediaGeneration: handleImageGeneration } = useMediaMessageHandler({
    type: 'image',
    selectedModel,
    aspectRatio,
    resolution,
    outputFormat,
    conversationTitle,
    onMessagePending,
    onMessageSent,
    onMediaTaskSubmitted,
  });

  // 视频消息处理（使用统一媒体处理器）
  const { handleMediaGeneration: handleVideoGeneration } = useMediaMessageHandler({
    type: 'video',
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
