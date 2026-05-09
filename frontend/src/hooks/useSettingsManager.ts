/**
 * 设置管理 Hook
 *
 * 管理图像/视频/聊天参数状态，支持持久化存储
 */

import { useState, useCallback, useEffect, useRef } from 'react';
import {
  type AspectRatio,
  type ImageResolution,
  type ImageOutputFormat,
  type ImageCount,
  type VideoFrames,
  type VideoAspectRatio,
} from '../constants/models';
import {
  getSavedSettings,
  saveSettings as persistSettings,
  resetSettings as clearSettings,
  type UserAdvancedSettings,
} from '../utils/settingsStorage';
import { updateConversation, type ChatSettings as ConversationChatSettings } from '../services/conversation';
import { logger } from '../utils/logger';

// ============================================================
// 类型定义
// ============================================================

export interface ImageSettings {
  aspectRatio: AspectRatio;
  resolution: ImageResolution;
  outputFormat: ImageOutputFormat;
  numImages: ImageCount;
}

export interface VideoSettings {
  frames: VideoFrames;
  aspectRatio: VideoAspectRatio;
  removeWatermark: boolean;
}

export type PermissionMode = 'auto' | 'ask' | 'plan';

export interface ChatSettings {
  thinkingEffort: 'minimal' | 'low' | 'medium' | 'high';
  deepThinkMode: boolean;
  permissionMode: PermissionMode;  // 权限模式：auto/ask/plan
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

/** 系统默认值（新建对话时使用） */
const DEFAULTS = {
  image: { aspectRatio: '1:1' as AspectRatio, resolution: '1024x1024' as ImageResolution, outputFormat: 'png' as ImageOutputFormat, numImages: 1 as ImageCount },
  video: { frames: '10' as VideoFrames, aspectRatio: 'landscape' as VideoAspectRatio, removeWatermark: false },
  chat: { thinkingEffort: 'low' as const, deepThinkMode: true, permissionMode: 'auto' as PermissionMode, temperature: 1.0, topP: 0.95, topK: 40, maxOutputTokens: 8192 },
};

export function useSettingsManager(
  conversationId?: string | null,
  conversationChatSettings?: ConversationChatSettings | null,
): UseSettingsManagerReturn {
  // 加载保存的设置（localStorage 作为全局默认的兜底）
  const savedSettings = getSavedSettings();
  // 对话级设置（优先）> localStorage（兜底）> 系统默认值
  const cs = conversationChatSettings;

  // 图像生成参数
  const [imageSettings, setImageSettings] = useState<ImageSettings>({
    aspectRatio: (cs?.image_aspect_ratio as AspectRatio) || savedSettings.image.aspectRatio,
    resolution: (cs?.image_resolution as ImageResolution) || savedSettings.image.resolution,
    outputFormat: (cs?.image_output_format as ImageOutputFormat) || savedSettings.image.outputFormat,
    numImages: (cs?.image_num_images as ImageCount) ?? savedSettings.image.numImages,
  });

  // 视频生成参数
  const [videoSettings, setVideoSettings] = useState<VideoSettings>({
    frames: (cs?.video_frames as VideoFrames) ?? savedSettings.video.frames,
    aspectRatio: (cs?.video_aspect_ratio as VideoAspectRatio) || savedSettings.video.aspectRatio,
    removeWatermark: cs?.video_remove_watermark ?? savedSettings.video.removeWatermark,
  });

  // 聊天模型参数
  const [chatSettings, setChatSettings] = useState<ChatSettings>({
    thinkingEffort: (cs?.thinking_effort as ChatSettings['thinkingEffort']) || savedSettings.chat?.thinkingEffort || DEFAULTS.chat.thinkingEffort,
    deepThinkMode: cs?.deep_think_mode ?? true,
    permissionMode: (savedSettings.chat as any)?.permissionMode || DEFAULTS.chat.permissionMode,
    temperature: cs?.temperature ?? savedSettings.chat?.temperature ?? DEFAULTS.chat.temperature,
    topP: cs?.top_p ?? savedSettings.chat?.topP ?? DEFAULTS.chat.topP,
    topK: cs?.top_k ?? savedSettings.chat?.topK ?? DEFAULTS.chat.topK,
    maxOutputTokens: cs?.max_output_tokens ?? savedSettings.chat?.maxOutputTokens ?? DEFAULTS.chat.maxOutputTokens,
  });

  // 切换对话时恢复设置
  const prevConversationId = useRef<string | null | undefined>(undefined);
  useEffect(() => {
    if (conversationId === prevConversationId.current) return;
    prevConversationId.current = conversationId;
    const s = conversationChatSettings;
    // 有对话级设置 → 恢复；无（新对话）→ 用系统默认值
    setImageSettings({
      aspectRatio: (s?.image_aspect_ratio as AspectRatio) || DEFAULTS.image.aspectRatio,
      resolution: (s?.image_resolution as ImageResolution) || DEFAULTS.image.resolution,
      outputFormat: (s?.image_output_format as ImageOutputFormat) || DEFAULTS.image.outputFormat,
      numImages: (s?.image_num_images as ImageCount) ?? DEFAULTS.image.numImages,
    });
    setVideoSettings({
      frames: (s?.video_frames as VideoFrames) ?? DEFAULTS.video.frames,
      aspectRatio: (s?.video_aspect_ratio as VideoAspectRatio) || DEFAULTS.video.aspectRatio,
      removeWatermark: s?.video_remove_watermark ?? DEFAULTS.video.removeWatermark,
    });
    setChatSettings({
      thinkingEffort: (s?.thinking_effort as ChatSettings['thinkingEffort']) || DEFAULTS.chat.thinkingEffort,
      deepThinkMode: s?.deep_think_mode ?? DEFAULTS.chat.deepThinkMode,
      permissionMode: DEFAULTS.chat.permissionMode,
      temperature: s?.temperature ?? DEFAULTS.chat.temperature,
      topP: s?.top_p ?? DEFAULTS.chat.topP,
      topK: s?.top_k ?? DEFAULTS.chat.topK,
      maxOutputTokens: s?.max_output_tokens ?? DEFAULTS.chat.maxOutputTokens,
    });
  }, [conversationId, conversationChatSettings]);

  // 自动保存到对话（debounce 避免频繁请求）
  const saveTimerRef = useRef<ReturnType<typeof setTimeout>>();
  const autoSaveToConversation = useCallback((
    img: ImageSettings, vid: VideoSettings, chat: ChatSettings,
  ) => {
    if (!conversationId) return;
    clearTimeout(saveTimerRef.current);
    saveTimerRef.current = setTimeout(() => {
      const payload: ConversationChatSettings = {
        deep_think_mode: chat.deepThinkMode,
        thinking_effort: chat.thinkingEffort,
        temperature: chat.temperature,
        top_p: chat.topP,
        top_k: chat.topK,
        max_output_tokens: chat.maxOutputTokens,
        image_aspect_ratio: img.aspectRatio,
        image_resolution: img.resolution,
        image_output_format: img.outputFormat,
        image_num_images: img.numImages,
        video_frames: vid.frames,
        video_aspect_ratio: vid.aspectRatio,
        video_remove_watermark: vid.removeWatermark,
      };
      updateConversation(conversationId, { chat_settings: payload })
        .catch((e) => logger.error('settings', '保存对话设置失败', e));
    }, 500);
  }, [conversationId]);

  // 设置单个图像参数
  const setImageSetting = useCallback(
    <K extends keyof ImageSettings>(key: K, value: ImageSettings[K]) => {
      setImageSettings((prev) => {
        const next = { ...prev, [key]: value };
        autoSaveToConversation(next, videoSettings, chatSettings);
        return next;
      });
    },
    [autoSaveToConversation, videoSettings, chatSettings]
  );

  // 设置单个视频参数
  const setVideoSetting = useCallback(
    <K extends keyof VideoSettings>(key: K, value: VideoSettings[K]) => {
      setVideoSettings((prev) => {
        const next = { ...prev, [key]: value };
        autoSaveToConversation(imageSettings, next, chatSettings);
        return next;
      });
    },
    [autoSaveToConversation, imageSettings, chatSettings]
  );

  // 设置单个聊天参数
  const setChatSetting = useCallback(
    <K extends keyof ChatSettings>(key: K, value: ChatSettings[K]) => {
      setChatSettings((prev) => {
        const next = { ...prev, [key]: value };
        autoSaveToConversation(imageSettings, videoSettings, next);
        return next;
      });
    },
    [autoSaveToConversation, imageSettings, videoSettings]
  );

  // 保存当前设置为默认值
  const saveSettings = useCallback(() => {
    const settings: UserAdvancedSettings = {
      image: {
        aspectRatio: imageSettings.aspectRatio,
        resolution: imageSettings.resolution,
        outputFormat: imageSettings.outputFormat,
        numImages: imageSettings.numImages,
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
      numImages: defaults.image.numImages,
    });
    setVideoSettings({
      frames: defaults.video.frames,
      aspectRatio: defaults.video.aspectRatio,
      removeWatermark: defaults.video.removeWatermark,
    });
    setChatSettings({
      thinkingEffort: defaults.chat.thinkingEffort,
      deepThinkMode: true,
      permissionMode: 'auto',
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
