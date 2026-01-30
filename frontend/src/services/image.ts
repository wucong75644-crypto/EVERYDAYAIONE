/**
 * 图像生成 API 服务
 */

import { request } from './api';
import type { TaskStatus } from '../types/task';

// ============================================================
// 类型定义
// ============================================================

export type ImageModel = 'google/nano-banana' | 'google/nano-banana-edit' | 'nano-banana-pro';

export type AspectRatio = '1:1' | '9:16' | '16:9' | '3:4' | '4:3' | '2:3' | '3:2' | '4:5' | '5:4' | '21:9' | 'auto';

export type ImageResolution = '1K' | '2K' | '4K';

export type ImageOutputFormat = 'png' | 'jpeg' | 'jpg';

export type { TaskStatus };

export interface GenerateImageRequest {
  prompt: string;
  model?: ImageModel;
  size?: AspectRatio;
  output_format?: ImageOutputFormat;
  resolution?: ImageResolution;
  wait_for_result?: boolean;
  conversation_id?: string;
}

export interface EditImageRequest {
  prompt: string;
  image_urls: string[];
  size?: AspectRatio;
  output_format?: ImageOutputFormat;
  wait_for_result?: boolean;
  conversation_id?: string;
}

export interface UploadImageRequest {
  image_data: string;
}

export interface UploadImageResponse {
  url: string;
}

export interface GenerateImageResponse {
  task_id: string;
  status: TaskStatus;
  image_urls: string[];
  credits_consumed: number;
  cost_usd: number;
  cost_time_ms?: number;
}

export interface TaskStatusResponse {
  task_id: string;
  status: TaskStatus;
  image_urls: string[];
  fail_code?: string;
  fail_msg?: string;
}

export interface ImageModelInfo {
  model_id: string;
  description: string;
  requires_image_input: boolean;
  supported_sizes: string[];
  supported_formats: string[];
  supports_resolution: boolean;
  credits_per_image: number | Record<string, number>;
}

export interface ImageModelsResponse {
  models: ImageModelInfo[];
}

// ============================================================
// API 方法
// ============================================================

/**
 * 生成图像
 */
export async function generateImage(data: GenerateImageRequest): Promise<GenerateImageResponse> {
  return request<GenerateImageResponse>({
    method: 'POST',
    url: '/images/generate',
    data,
  });
}

/**
 * 编辑图像
 */
export async function editImage(data: EditImageRequest): Promise<GenerateImageResponse> {
  return request<GenerateImageResponse>({
    method: 'POST',
    url: '/images/edit',
    data,
  });
}

/**
 * 上传图片
 *
 * 将 base64 图片数据上传到存储服务，返回公开 URL。
 */
export async function uploadImage(imageData: string): Promise<UploadImageResponse> {
  return request<UploadImageResponse>({
    method: 'POST',
    url: '/images/upload',
    data: { image_data: imageData },
  });
}

/**
 * 查询任务状态
 */
export async function queryTaskStatus(taskId: string): Promise<TaskStatusResponse> {
  return request<TaskStatusResponse>({
    method: 'GET',
    url: `/images/tasks/${taskId}`,
    timeout: 120000, // 120秒超时（首次查询可能需要下载并上传到 OSS）
  });
}

/**
 * 获取可用的图像模型列表
 */
export async function getImageModels(): Promise<ImageModelsResponse> {
  return request<ImageModelsResponse>({
    method: 'GET',
    url: '/images/models',
  });
}

// ============================================================
// 轮询工具
// ============================================================

export interface PollOptions {
  /** 轮询间隔（毫秒），默认 2000 */
  interval?: number;
  /** 最大等待时间（毫秒），默认 300000 (5分钟) */
  maxWait?: number;
  /** 状态更新回调 */
  onStatusChange?: (status: TaskStatus) => void;
}

/**
 * 轮询任务直到完成
 */
export async function pollTaskUntilDone(
  taskId: string,
  options: PollOptions = {}
): Promise<TaskStatusResponse> {
  const { interval = 2000, maxWait = 300000, onStatusChange } = options;
  const startTime = Date.now();

  while (true) {
    const result = await queryTaskStatus(taskId);

    onStatusChange?.(result.status);

    if (result.status === 'success' || result.status === 'failed' || result.status === 'timeout') {
      return result;
    }

    if (Date.now() - startTime > maxWait) {
      throw new Error('任务等待超时');
    }

    await new Promise((resolve) => setTimeout(resolve, interval));
  }
}

// ============================================================
// 便捷方法
// ============================================================

/**
 * 生成图像并等待结果（自动处理轮询）
 */
export async function generateImageAndWait(
  data: Omit<GenerateImageRequest, 'wait_for_result'>,
  options?: PollOptions
): Promise<GenerateImageResponse> {
  // 先尝试同步等待
  const response = await generateImage({ ...data, wait_for_result: true });

  // 如果状态是 pending 或 processing，需要轮询
  if (response.status === 'pending' || response.status === 'processing') {
    const result = await pollTaskUntilDone(response.task_id, options);
    return {
      ...response,
      status: result.status,
      image_urls: result.image_urls,
    };
  }

  return response;
}

// ============================================================
// 模型配置（前端展示用）
// ============================================================

export const IMAGE_MODELS = [
  {
    id: 'google/nano-banana' as ImageModel,
    name: 'Nano Banana',
    description: '基础文生图 (Gemini 2.5)',
    credits: 4, // ~$0.02 per image
    requiresImage: false,
    supportsResolution: false,
  },
  {
    id: 'google/nano-banana-edit' as ImageModel,
    name: 'Nano Banana Edit',
    description: '图像编辑 (需要上传图片)',
    credits: 4, // ~$0.02 per image
    requiresImage: true,
    supportsResolution: false,
  },
  {
    id: 'nano-banana-pro' as ImageModel,
    name: 'Nano Banana Pro',
    description: '高级文生图 (Gemini 3 Pro, 支持4K)',
    credits: { '1K': 18, '2K': 18, '4K': 24 }, // 1K/2K: ~$0.09, 4K: ~$0.12
    requiresImage: false,
    supportsResolution: true,
  },
];

export const ASPECT_RATIOS = [
  { value: '1:1' as AspectRatio, label: '1:1 (方形)' },
  { value: '2:3' as AspectRatio, label: '2:3 (竖版)' },
  { value: '3:2' as AspectRatio, label: '3:2 (横版)' },
  { value: '3:4' as AspectRatio, label: '3:4 (肖像)' },
  { value: '4:3' as AspectRatio, label: '4:3 (经典)' },
  { value: '4:5' as AspectRatio, label: '4:5 (短竖)' },
  { value: '5:4' as AspectRatio, label: '5:4 (短横)' },
  { value: '9:16' as AspectRatio, label: '9:16 (手机竖屏)' },
  { value: '16:9' as AspectRatio, label: '16:9 (宽屏)' },
  { value: '21:9' as AspectRatio, label: '21:9 (超宽)' },
  { value: 'auto' as AspectRatio, label: 'Auto (自动)' },
];

export const RESOLUTIONS = [
  { value: '1K' as ImageResolution, label: '1K', credits: 18 }, // ~$0.09
  { value: '2K' as ImageResolution, label: '2K', credits: 18 }, // ~$0.09
  { value: '4K' as ImageResolution, label: '4K', credits: 24 }, // ~$0.12
];

export const OUTPUT_FORMATS = [
  { value: 'png' as ImageOutputFormat, label: 'PNG' },
  { value: 'jpeg' as ImageOutputFormat, label: 'JPEG' },
];
