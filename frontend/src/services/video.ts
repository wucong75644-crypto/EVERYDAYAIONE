/**
 * 视频生成 API 服务
 */

import { request } from './api';

// ============================================================
// 类型定义
// ============================================================

export type VideoModel = 'sora-2-text-to-video' | 'sora-2-image-to-video' | 'sora-2-pro-storyboard';

export type VideoFrames = '10' | '15' | '25';

export type VideoAspectRatio = 'portrait' | 'landscape';

export type TaskStatus = 'pending' | 'processing' | 'success' | 'failed' | 'timeout';

export interface GenerateTextToVideoRequest {
  prompt: string;
  model?: VideoModel;
  n_frames?: VideoFrames;
  aspect_ratio?: VideoAspectRatio;
  remove_watermark?: boolean;
  wait_for_result?: boolean;
}

export interface GenerateImageToVideoRequest {
  prompt: string;
  image_url: string;
  model?: VideoModel;
  n_frames?: VideoFrames;
  aspect_ratio?: VideoAspectRatio;
  remove_watermark?: boolean;
  wait_for_result?: boolean;
}

export interface GenerateStoryboardVideoRequest {
  n_frames?: VideoFrames;
  storyboard_images?: string[];
  aspect_ratio?: VideoAspectRatio;
  wait_for_result?: boolean;
}

export interface GenerateVideoResponse {
  task_id: string;
  status: TaskStatus;
  video_url: string | null;
  duration_seconds: number;
  credits_consumed: number;
  cost_usd: number;
  cost_time_ms?: number;
}

export interface TaskStatusResponse {
  task_id: string;
  status: TaskStatus;
  video_url: string | null;
  fail_code?: string;
  fail_msg?: string;
}

export interface VideoModelInfo {
  model_id: string;
  description: string;
  requires_image_input: boolean;
  requires_prompt: boolean;
  supported_frames: string[];
  supports_watermark_removal: boolean;
  credits_per_second: number;
}

export interface VideoModelsResponse {
  models: VideoModelInfo[];
}

// ============================================================
// API 方法
// ============================================================

/**
 * 文本生成视频
 */
export async function generateTextToVideo(data: GenerateTextToVideoRequest): Promise<GenerateVideoResponse> {
  return request<GenerateVideoResponse>({
    method: 'POST',
    url: '/videos/generate/text-to-video',
    data,
  });
}

/**
 * 图片生成视频
 */
export async function generateImageToVideo(data: GenerateImageToVideoRequest): Promise<GenerateVideoResponse> {
  return request<GenerateVideoResponse>({
    method: 'POST',
    url: '/videos/generate/image-to-video',
    data,
  });
}

/**
 * 故事板视频生成
 */
export async function generateStoryboardVideo(data: GenerateStoryboardVideoRequest): Promise<GenerateVideoResponse> {
  return request<GenerateVideoResponse>({
    method: 'POST',
    url: '/videos/generate/storyboard',
    data,
  });
}

/**
 * 查询任务状态
 */
export async function queryVideoTaskStatus(taskId: string): Promise<TaskStatusResponse> {
  return request<TaskStatusResponse>({
    method: 'GET',
    url: `/videos/tasks/${taskId}`,
  });
}

/**
 * 获取可用的视频模型列表
 */
export async function getVideoModels(): Promise<VideoModelsResponse> {
  return request<VideoModelsResponse>({
    method: 'GET',
    url: '/videos/models',
  });
}

// ============================================================
// 轮询工具
// ============================================================

export interface PollOptions {
  /** 轮询间隔（毫秒），默认 5000 (视频生成较慢) */
  interval?: number;
  /** 最大等待时间（毫秒），默认 600000 (10分钟) */
  maxWait?: number;
  /** 状态更新回调 */
  onStatusChange?: (status: TaskStatus) => void;
}

/**
 * 轮询视频任务直到完成
 */
export async function pollVideoTaskUntilDone(
  taskId: string,
  options: PollOptions = {}
): Promise<TaskStatusResponse> {
  const { interval = 5000, maxWait = 600000, onStatusChange } = options;
  const startTime = Date.now();

  while (true) {
    const result = await queryVideoTaskStatus(taskId);

    onStatusChange?.(result.status);

    if (result.status === 'success' || result.status === 'failed' || result.status === 'timeout') {
      return result;
    }

    if (Date.now() - startTime > maxWait) {
      throw new Error('视频生成等待超时');
    }

    await new Promise((resolve) => setTimeout(resolve, interval));
  }
}

// ============================================================
// 便捷方法
// ============================================================

/**
 * 生成视频并等待结果（自动处理轮询）
 */
export async function generateTextToVideoAndWait(
  data: Omit<GenerateTextToVideoRequest, 'wait_for_result'>,
  options?: PollOptions
): Promise<GenerateVideoResponse> {
  // 先异步创建任务
  const response = await generateTextToVideo({ ...data, wait_for_result: false });

  // 轮询直到完成
  const result = await pollVideoTaskUntilDone(response.task_id, options);

  return {
    ...response,
    status: result.status,
    video_url: result.video_url,
  };
}

/**
 * 图生视频并等待结果（自动处理轮询）
 */
export async function generateImageToVideoAndWait(
  data: Omit<GenerateImageToVideoRequest, 'wait_for_result'>,
  options?: PollOptions
): Promise<GenerateVideoResponse> {
  // 先异步创建任务
  const response = await generateImageToVideo({ ...data, wait_for_result: false });

  // 轮询直到完成
  const result = await pollVideoTaskUntilDone(response.task_id, options);

  return {
    ...response,
    status: result.status,
    video_url: result.video_url,
  };
}

// ============================================================
// 模型配置（前端展示用）
// ============================================================

export const VIDEO_MODELS = [
  {
    id: 'sora-2-text-to-video' as VideoModel,
    name: 'Sora 2 Text-to-Video',
    description: '文本生成视频',
    creditsPerSecond: 3,
    requiresImage: false,
    requiresPrompt: true,
    supportedDurations: [10, 15],
    supportsWatermarkRemoval: true,
  },
  {
    id: 'sora-2-image-to-video' as VideoModel,
    name: 'Sora 2 Image-to-Video',
    description: '图片生成视频',
    creditsPerSecond: 3,
    requiresImage: true,
    requiresPrompt: true,
    supportedDurations: [10, 15],
    supportsWatermarkRemoval: true,
  },
  {
    id: 'sora-2-pro-storyboard' as VideoModel,
    name: 'Sora 2 Pro Storyboard',
    description: '专业故事板视频',
    creditsPerSecond: 15,
    requiresImage: false,
    requiresPrompt: false,
    supportedDurations: [10, 15, 25],
    supportsWatermarkRemoval: false,
  },
];

export const VIDEO_DURATIONS = [
  { value: '10' as VideoFrames, label: '10秒', credits: 30 }, // Text/Image-to-Video 基准价格
  { value: '15' as VideoFrames, label: '15秒', credits: 45 }, // Text/Image-to-Video 基准价格
  { value: '25' as VideoFrames, label: '25秒', credits: 270, note: '仅故事板' }, // Pro Storyboard 价格
];

export const VIDEO_ASPECT_RATIOS = [
  { value: 'landscape' as VideoAspectRatio, label: '横屏' },
  { value: 'portrait' as VideoAspectRatio, label: '竖屏' },
];
