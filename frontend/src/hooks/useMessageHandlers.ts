/**
 * 消息处理 Hook（组合器）
 *
 * 组合聊天、图像生成、视频生成的处理逻辑
 * 使用统一的 useMediaMessageHandler 处理图片/视频
 */

import { type UnifiedModel } from '../constants/models';
import { type Message } from '../stores/useMessageStore';
import {
  type AspectRatio,
  type ImageResolution,
  type ImageOutputFormat,
  type VideoFrames,
  type VideoAspectRatio,
} from '../constants/models';
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
  onMessagePending: (message: Message) => void;
  onMessageSent: (aiMessage?: Message | null) => void;
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
    onMessagePending,
    onMessageSent,
    onMediaTaskSubmitted,
  } = params;

  // 文本消息处理
  const { handleChatMessage } = useTextMessageHandler({
    selectedModel,
    thinkingEffort,
    deepThinkMode,
    onMessagePending,
    onMessageSent,
  });

  // 图片消息处理（使用统一媒体处理器）
  const { handleMediaGeneration: handleImageGeneration } = useMediaMessageHandler({
    type: 'image',
    selectedModel,
    aspectRatio,
    resolution,
    outputFormat,
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
