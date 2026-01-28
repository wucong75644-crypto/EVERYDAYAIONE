/**
 * 用户设置本地存储工具
 *
 * 保存和读取用户的默认参数设置
 */

import { type AspectRatio, type ImageResolution, type ImageOutputFormat } from '../services/image';
import { type VideoFrames, type VideoAspectRatio } from '../services/video';

// 存储键名
const SETTINGS_KEY = 'user_advanced_settings';

// 设置接口
export interface UserAdvancedSettings {
  image: {
    aspectRatio: AspectRatio;
    resolution: ImageResolution;
    outputFormat: ImageOutputFormat;
  };
  video: {
    frames: VideoFrames;
    aspectRatio: VideoAspectRatio;
    removeWatermark: boolean;
  };
  chat: {
    thinkingEffort: 'minimal' | 'low' | 'medium' | 'high';
  };
}

// 默认设置
const DEFAULT_SETTINGS: UserAdvancedSettings = {
  image: {
    aspectRatio: '1:1',
    resolution: '1K',
    outputFormat: 'png',
  },
  video: {
    frames: '10',
    aspectRatio: 'landscape',
    removeWatermark: true,
  },
  chat: {
    thinkingEffort: 'low', // 标准速度（默认）
  },
};

/**
 * 获取保存的设置
 */
export function getSavedSettings(): UserAdvancedSettings {
  try {
    const saved = localStorage.getItem(SETTINGS_KEY);
    if (saved) {
      const parsed = JSON.parse(saved);
      // 合并默认值以确保所有字段都存在
      return {
        image: { ...DEFAULT_SETTINGS.image, ...parsed.image },
        video: { ...DEFAULT_SETTINGS.video, ...parsed.video },
        chat: { ...DEFAULT_SETTINGS.chat, ...parsed.chat },
      };
    }
  } catch (error) {
    console.error('读取设置失败:', error);
  }
  return DEFAULT_SETTINGS;
}

/**
 * 保存设置
 */
export function saveSettings(settings: UserAdvancedSettings): void {
  try {
    localStorage.setItem(SETTINGS_KEY, JSON.stringify(settings));
  } catch (error) {
    console.error('保存设置失败:', error);
  }
}

/**
 * 重置为默认设置
 */
export function resetSettings(): UserAdvancedSettings {
  try {
    localStorage.removeItem(SETTINGS_KEY);
  } catch (error) {
    console.error('重置设置失败:', error);
  }
  return DEFAULT_SETTINGS;
}

// ============================================================
// 占位符尺寸计算工具
// ============================================================

/** 占位符尺寸 */
export interface PlaceholderSize {
  width: number;
  height: number;
}

/** 占位符基准尺寸（最小边长度） */
const PLACEHOLDER_BASE_SIZE = 180;
/** 视频占位符最大宽度（横屏） */
const VIDEO_MAX_WIDTH = 427;
/** 视频占位符宽度（竖屏） */
const VIDEO_PORTRAIT_WIDTH = 261;

/**
 * 根据图片宽高比计算占位符尺寸
 * @param aspectRatio 图片宽高比
 * @returns 占位符尺寸（宽 × 高）
 */
export function getImagePlaceholderSize(aspectRatio: AspectRatio): PlaceholderSize {
  // 宽高比映射表：[宽比例, 高比例]
  const ratioMap: Record<AspectRatio, [number, number]> = {
    '1:1': [1, 1],
    '9:16': [9, 16],
    '16:9': [16, 9],
    '3:4': [3, 4],
    '4:3': [4, 3],
    '2:3': [2, 3],
    '3:2': [3, 2],
    '4:5': [4, 5],
    '5:4': [5, 4],
    '21:9': [21, 9],
    'auto': [1, 1], // auto 默认为 1:1
  };

  const [w, h] = ratioMap[aspectRatio] || [1, 1];

  // 以 PLACEHOLDER_BASE_SIZE 为基准计算尺寸
  // 横版（宽>高）：高度固定为 base，宽度按比例
  // 竖版（高>宽）：宽度固定为 base，高度按比例
  // 方形：宽高都是 base
  if (w > h) {
    // 横版：高度固定
    return {
      width: Math.round(PLACEHOLDER_BASE_SIZE * (w / h)),
      height: PLACEHOLDER_BASE_SIZE,
    };
  } else if (h > w) {
    // 竖版：宽度固定
    return {
      width: PLACEHOLDER_BASE_SIZE,
      height: Math.round(PLACEHOLDER_BASE_SIZE * (h / w)),
    };
  } else {
    // 方形
    return {
      width: PLACEHOLDER_BASE_SIZE,
      height: PLACEHOLDER_BASE_SIZE,
    };
  }
}

/**
 * 根据视频宽高比计算占位符尺寸
 * @param aspectRatio 视频宽高比（横屏/竖屏）
 * @returns 占位符尺寸（宽 × 高）
 */
export function getVideoPlaceholderSize(aspectRatio: VideoAspectRatio): PlaceholderSize {
  if (aspectRatio === 'landscape') {
    // 横屏 16:9
    return {
      width: VIDEO_MAX_WIDTH,
      height: Math.round(VIDEO_MAX_WIDTH * (9 / 16)),
    };
  } else {
    // 竖屏 9:16
    return {
      width: VIDEO_PORTRAIT_WIDTH,
      height: Math.round(VIDEO_PORTRAIT_WIDTH * (16 / 9)),
    };
  }
}
