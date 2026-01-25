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
