/**
 * 媒体生成选项类型和常量
 *
 * UI 下拉框使用的选项配置（图像/视频参数）
 */

// ============================================================
// 类型定义
// ============================================================

/** 图像宽高比 */
export type AspectRatio = '1:1' | '9:16' | '16:9' | '3:4' | '4:3' | '2:3' | '3:2' | '4:5' | '5:4' | '21:9' | 'auto';

/** 图像分辨率 */
export type ImageResolution = '1K' | '2K' | '4K';

/** 图像输出格式 */
export type ImageOutputFormat = 'png' | 'jpeg' | 'jpg';

/** 视频时长 */
export type VideoFrames = '10' | '15' | '25';

/** 生成数量（图片） */
export type ImageCount = 1 | 2 | 3 | 4;

/** 视频宽高比 */
export type VideoAspectRatio = 'portrait' | 'landscape';

// ============================================================
// 选项常量（UI 下拉框使用）
// ============================================================

/** 宽高比选项 */
export const ASPECT_RATIOS: { value: AspectRatio; label: string }[] = [
  { value: '1:1', label: '1:1 (方形)' },
  { value: '2:3', label: '2:3 (竖版)' },
  { value: '3:2', label: '3:2 (横版)' },
  { value: '3:4', label: '3:4 (肖像)' },
  { value: '4:3', label: '4:3 (经典)' },
  { value: '4:5', label: '4:5 (短竖)' },
  { value: '5:4', label: '5:4 (短横)' },
  { value: '9:16', label: '9:16 (手机竖屏)' },
  { value: '16:9', label: '16:9 (宽屏)' },
  { value: '21:9', label: '21:9 (超宽)' },
  { value: 'auto', label: 'Auto (自动)' },
];

/** 分辨率选项 */
export const RESOLUTIONS: { value: ImageResolution; label: string; credits: number }[] = [
  { value: '1K', label: '1K', credits: 18 },
  { value: '2K', label: '2K', credits: 18 },
  { value: '4K', label: '4K', credits: 24 },
];

/** 输出格式选项 */
export const OUTPUT_FORMATS: { value: ImageOutputFormat; label: string }[] = [
  { value: 'png', label: 'PNG' },
  { value: 'jpeg', label: 'JPEG' },
];

/** 生成数量选项 */
export const IMAGE_COUNTS: { value: ImageCount; label: string }[] = [
  { value: 1, label: '1张' },
  { value: 2, label: '2张' },
  { value: 3, label: '3张' },
  { value: 4, label: '4张' },
];

/** 视频时长选项 */
export const VIDEO_DURATIONS: { value: VideoFrames; label: string; credits: number; note?: string }[] = [
  { value: '10', label: '10秒', credits: 30 },
  { value: '15', label: '15秒', credits: 45 },
  { value: '25', label: '25秒', credits: 270, note: '仅故事板' },
];

/** 视频宽高比选项 */
export const VIDEO_ASPECT_RATIOS: { value: VideoAspectRatio; label: string }[] = [
  { value: 'landscape', label: '横屏' },
  { value: 'portrait', label: '竖屏' },
];
