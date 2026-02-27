/**
 * 设置管理 Hook
 *
 * 管理图像/视频/聊天参数状态，支持持久化存储
 */

import { useState, useCallback } from 'react';
import {
  type AspectRatio,
  type ImageResolution,
  type ImageOutputFormat,
  type VideoFrames,
  type VideoAspectRatio,
} from '../constants/models';
import {
  getSavedSettings,
  saveSettings as persistSettings,
  resetSettings as clearSettings,
  type UserAdvancedSettings,
} from '../utils/settingsStorage';

// ============================================================
// 类型定义
// ============================================================

export interface ImageSettings {
  aspectRatio: AspectRatio;
  resolution: ImageResolution;
  outputFormat: ImageOutputFormat;
}

export interface VideoSettings {
  frames: VideoFrames;
  aspectRatio: VideoAspectRatio;
  removeWatermark: boolean;
}

export interface ChatSettings {
  thinkingEffort: 'minimal' | 'low' | 'medium' | 'high';
  deepThinkMode: boolean;
  temperature: number;      // 0.0 - 2.0
  topP: number;            // 0.0 - 1.0
  topK: number;            // 1 - 64
  maxOutputTokens: number; // 1 - 65536
}

export interface UseSettingsManagerReturn {
  // 图像设置
  imageSettings: ImageSettings;
  setImageSetting: <K extends keyof ImageSettings>(key: K, value: ImageSettings[K]) => void;

  // 视频设置
  videoSettings: VideoSettings;
  setVideoSetting: <K extends keyof VideoSettings>(key: K, value: VideoSettings[K]) => void;

  // 聊天设置
  chatSettings: ChatSettings;
  setChatSetting: <K extends keyof ChatSettings>(key: K, value: ChatSettings[K]) => void;

  // 持久化操作
  saveSettings: () => void;
  resetSettings: () => void;
}

// ============================================================
// Hook 实现
// ============================================================

export function useSettingsManager(): UseSettingsManagerReturn {
  // 加载保存的设置
  const savedSettings = getSavedSettings();

  // 图像生成参数
  const [imageSettings, setImageSettings] = useState<ImageSettings>({
    aspectRatio: savedSettings.image.aspectRatio,
    resolution: savedSettings.image.resolution,
    outputFormat: savedSettings.image.outputFormat,
  });

  // 视频生成参数
  const [videoSettings, setVideoSettings] = useState<VideoSettings>({
    frames: savedSettings.video.frames,
    aspectRatio: savedSettings.video.aspectRatio,
    removeWatermark: savedSettings.video.removeWatermark,
  });

  // 聊天模型参数
  const [chatSettings, setChatSettings] = useState<ChatSettings>({
    thinkingEffort: savedSettings.chat?.thinkingEffort || 'low',
    deepThinkMode: false, // 非持久化字段，每次会话重置
    temperature: savedSettings.chat?.temperature ?? 1.0,
    topP: savedSettings.chat?.topP ?? 0.95,
    topK: savedSettings.chat?.topK ?? 40,
    maxOutputTokens: savedSettings.chat?.maxOutputTokens ?? 8192,
  });

  // 设置单个图像参数
  const setImageSetting = useCallback(
    <K extends keyof ImageSettings>(key: K, value: ImageSettings[K]) => {
      setImageSettings((prev) => ({ ...prev, [key]: value }));
    },
    []
  );

  // 设置单个视频参数
  const setVideoSetting = useCallback(
    <K extends keyof VideoSettings>(key: K, value: VideoSettings[K]) => {
      setVideoSettings((prev) => ({ ...prev, [key]: value }));
    },
    []
  );

  // 设置单个聊天参数
  const setChatSetting = useCallback(
    <K extends keyof ChatSettings>(key: K, value: ChatSettings[K]) => {
      setChatSettings((prev) => ({ ...prev, [key]: value }));
    },
    []
  );

  // 保存当前设置为默认值
  const saveSettings = useCallback(() => {
    const settings: UserAdvancedSettings = {
      image: {
        aspectRatio: imageSettings.aspectRatio,
        resolution: imageSettings.resolution,
        outputFormat: imageSettings.outputFormat,
      },
      video: {
        frames: videoSettings.frames,
        aspectRatio: videoSettings.aspectRatio,
        removeWatermark: videoSettings.removeWatermark,
      },
      chat: {
        thinkingEffort: chatSettings.thinkingEffort,
        temperature: chatSettings.temperature,
        topP: chatSettings.topP,
        topK: chatSettings.topK,
        maxOutputTokens: chatSettings.maxOutputTokens,
      },
    };
    persistSettings(settings);
  }, [imageSettings, videoSettings, chatSettings]);

  // 重置为默认设置
  const resetSettings = useCallback(() => {
    const defaults = clearSettings();
    setImageSettings({
      aspectRatio: defaults.image.aspectRatio,
      resolution: defaults.image.resolution,
      outputFormat: defaults.image.outputFormat,
    });
    setVideoSettings({
      frames: defaults.video.frames,
      aspectRatio: defaults.video.aspectRatio,
      removeWatermark: defaults.video.removeWatermark,
    });
    setChatSettings({
      thinkingEffort: defaults.chat.thinkingEffort,
      deepThinkMode: false,
      temperature: defaults.chat.temperature,
      topP: defaults.chat.topP,
      topK: defaults.chat.topK,
      maxOutputTokens: defaults.chat.maxOutputTokens,
    });
  }, []);

  return {
    imageSettings,
    setImageSetting,
    videoSettings,
    setVideoSetting,
    chatSettings,
    setChatSetting,
    saveSettings,
    resetSettings,
  };
}
