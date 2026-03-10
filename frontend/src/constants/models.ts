/**
 * 模型定义和类型 — 重导出中心
 *
 * 所有外部 import 统一从此文件导入，内部按类型拆分到子模块。
 */

// 类型（从 modelTypes 导出）
export type { ModelType, ModelCapabilities, UnifiedModel } from './modelTypes';

// 媒体选项类型和常量（从 mediaOptions 导出）
export type {
  AspectRatio,
  ImageResolution,
  ImageOutputFormat,
  VideoFrames,
  ImageCount,
  VideoAspectRatio,
} from './mediaOptions';

export {
  ASPECT_RATIOS,
  RESOLUTIONS,
  OUTPUT_FORMATS,
  IMAGE_COUNTS,
  VIDEO_DURATIONS,
  VIDEO_ASPECT_RATIOS,
} from './mediaOptions';

// 模型配置
import { type UnifiedModel } from './modelTypes';
import { CHAT_MODELS } from './chatModels';
import { IMAGE_MODELS, VIDEO_MODELS } from './mediaModels';

// 合并所有模型
export const ALL_MODELS: UnifiedModel[] = [
  ...CHAT_MODELS,
  ...IMAGE_MODELS,
  ...VIDEO_MODELS,
];

// 根据订阅状态筛选可用模型（auto 智能模式始终可用）
export function getAvailableModels(
  hasImage: boolean,
  subscribedModelIds?: string[],
): UnifiedModel[] {
  void hasImage;
  if (!subscribedModelIds) return ALL_MODELS;
  return ALL_MODELS.filter(
    (m) => m.id === 'auto' || subscribedModelIds.includes(m.id),
  );
}
