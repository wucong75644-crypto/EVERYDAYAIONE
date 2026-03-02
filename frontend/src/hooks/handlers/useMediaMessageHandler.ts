/**
 * 统一媒体消息处理 Hook
 *
 * 合并图片/视频处理器，使用统一的 sendMessage API
 */

import { type UnifiedModel } from '../../constants/models';
import { type Message } from '../../stores/useMessageStore';
import { sendMessage, createTextContent, createTextWithImages, createErrorMessage, type GenerationType } from '../../services/messageSender';
import { useWebSocketContext } from '../../contexts/WebSocketContext';

export type MediaType = 'image' | 'video';

interface UseMediaMessageHandlerParams {
  type: MediaType;
  selectedModel: UnifiedModel;

  // 图片参数
  aspectRatio?: string;
  outputFormat?: string;
  resolution?: string;

  // 视频参数
  videoFrames?: string;
  videoAspectRatio?: string;
  removeWatermark?: boolean;

  // 回调
  onMessagePending: (message: Message) => void;
  onMessageSent: (aiMessage?: Message | null) => void;
  onMediaTaskSubmitted?: () => void;
}

export function useMediaMessageHandler(params: UseMediaMessageHandlerParams) {
  const {
    type,
    selectedModel,
    aspectRatio,
    resolution,
    outputFormat,
    videoFrames,
    videoAspectRatio,
    removeWatermark,
    onMessagePending,
    onMessageSent,
  } = params;

  // 获取 WebSocket 订阅/取消订阅函数
  const { subscribeTaskWithMapping, unsubscribeTask } = useWebSocketContext();

  const handleMediaGeneration = async (
    conversationId: string,
    prompt: string,
    imageUrls: string[] | null = null
  ) => {
    try {
      // 构建 content
      const content = imageUrls?.length
        ? createTextWithImages(prompt, imageUrls)
        : createTextContent(prompt);

      // 立即触发侧边栏乐观更新（不等待 API 返回）
      onMessagePending({
        id: 'temp-' + Date.now(),
        conversation_id: conversationId,
        role: 'user',
        content,
        status: 'completed',
        created_at: new Date().toISOString(),
      } as Message);

      // 构建类型特定参数（使用下划线命名匹配后端）
      const mediaParams: Record<string, unknown> = {};

      if (type === 'image') {
        mediaParams.aspect_ratio = aspectRatio ?? '1:1';
        mediaParams.resolution = resolution;
        mediaParams.output_format = outputFormat;
      } else if (type === 'video') {
        mediaParams.n_frames = videoFrames;
        mediaParams.aspect_ratio = videoAspectRatio;
        mediaParams.remove_watermark = removeWatermark;
      }

      // 调用统一发送器
      await sendMessage({
        conversationId,
        content,
        generationType: type as GenerationType,
        model: selectedModel.id,
        params: mediaParams,
        subscribeTask: subscribeTaskWithMapping,
        unsubscribeTask, // 🔥 传入取消订阅函数
      });

      // 注意：onMessageSent 现在由 WebSocketContext 处理
      // WebSocket 推送会触发相应的状态更新

    } catch (error) {
      console.error('Media generation failed:', error);
      onMessageSent(createErrorMessage(conversationId, error, '生成失败'));
    }
  };

  return { handleMediaGeneration };
}
